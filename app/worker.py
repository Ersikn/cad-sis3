import json
import logging
import os
import signal
from dataclasses import dataclass
from threading import Event

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Payment:
    payment_id: str
    account_id: str
    amount: int


def parse_payment(body):
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError('Payment must be a JSON object')
    payment_id = data.get('paymentId')
    account_id = data.get('account')
    amount = data.get('amount')
    if not all(isinstance(value, str) and 0 < len(value.strip()) <= 128
               for value in (payment_id, account_id)):
        raise ValueError('paymentId and account must be nonempty strings')
    if type(amount) is not int or not 0 < amount <= 10**12:
        raise ValueError('amount must be a positive integer in minor units')
    return Payment(payment_id.strip(), account_id.strip(), amount)


def apply_payment(db, processed_table, accounts_table, payment):
    payment_key = {'paymentId': {'S': payment.payment_id}}
    account_key = {'accountId': {'S': payment.account_id}}
    amount = {'N': str(payment.amount)}
    try:
        db.transact_write_items(TransactItems=[
            {'Put': {
                'TableName': processed_table,
                'Item': {**payment_key, **account_key, 'amount': amount},
                'ConditionExpression': 'attribute_not_exists(paymentId)',
            }},
            {'Update': {
                'TableName': accounts_table,
                'Key': account_key,
                'UpdateExpression': 'ADD balance :amount',
                'ExpressionAttributeValues': {':amount': amount},
            }},
        ])
    except db.exceptions.TransactionCanceledException:
        prior = db.get_item(
            TableName=processed_table, Key=payment_key, ConsistentRead=True,
        ).get('Item')
        if prior is None:
            raise
        if prior.get('accountId') != account_key['accountId'] or prior.get('amount') != amount:
            raise ValueError('paymentId was already used with different payment data')
        return 'duplicate_skipped'
    return 'processed'


def handle_message(sqs, db, queue_url, processed_table, accounts_table, message):
    try:
        payment = parse_payment(message['Body'])
        result = apply_payment(db, processed_table, accounts_table, payment)
    except ValueError as error:
        LOG.warning('invalid_message messageId=%s error=%s', message['MessageId'], error)
        return
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message['ReceiptHandle'])
    LOG.info('%s paymentId=%s account=%s amount=%s', result, payment.payment_id,
             payment.account_id, payment.amount)


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    queue_url = os.environ['PAYMENT_QUEUE_URL']
    processed_table = os.environ['PROCESSED_TABLE']
    accounts_table = os.environ['ACCOUNTS_TABLE']
    db = boto3.client('dynamodb', config=Config(connect_timeout=3, read_timeout=6,
                                               retries={'total_max_attempts': 2}))
    sqs = boto3.client('sqs', config=Config(connect_timeout=3, read_timeout=16,
                                          retries={'total_max_attempts': 2}))
    stopped = Event()

    def stop(*_):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info('worker_started')
    while not stopped.is_set():
        try:
            reply = sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=10,
                                        MaxNumberOfMessages=1)
            for message in reply.get('Messages', []):
                handle_message(sqs, db, queue_url, processed_table, accounts_table, message)
        except (BotoCoreError, ClientError):
            LOG.exception('aws_operation_failed')
            stopped.wait(2)


if __name__ == '__main__':
    main()
