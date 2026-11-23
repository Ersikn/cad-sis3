#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
import json
import time
from uuid import uuid4

import boto3


def wait_for(description, check, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(4)
    raise SystemExit(f'FAIL: {description} did not appear within {timeout}s')


def stack_outputs(client, stack):
    return {entry['OutputKey']: entry['OutputValue'] for entry in
            client.describe_stacks(StackName=stack)['Stacks'][0]['Outputs']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--messaging-stack', default='cad-sis3-messaging')
    parser.add_argument('--app-stack', default='cad-sis3-app-v2')
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    cfn = session.client('cloudformation')
    sns = session.client('sns')
    sqs = session.client('sqs')
    db = session.client('dynamodb')
    logs = session.client('logs')
    values = stack_outputs(cfn, args.messaging_stack)
    marker = datetime.now(timezone.utc).strftime('%m%d-%H%M%S') + '-' + uuid4().hex[:5]
    account = 'sis3-account-' + marker
    first = 'sis3-pay-a-' + marker
    second = 'sis3-pay-b-' + marker
    poison = 'sis3-invalid-' + marker
    log_start = int(time.time() * 1000) - 1000
    group = '/ecs/' + args.app_stack
    print(f'Run: {marker}\nAccount: {account}\nPayments: {first}, {second}\nInvalid body: {poison}', flush=True)

    def publish(message):
        sns.publish(TopicArn=values['TopicArn'], Message=message)

    def read_item(table, key, value):
        return db.get_item(TableName=values[table], Key={key: {'S': value}},
                           ConsistentRead=True).get('Item', {})

    def balance():
        item = read_item('AccountsTableName', 'accountId', account)
        return int(item.get('balance', {}).get('N', 0))

    def find_duplicate():
        result = logs.filter_log_events(logGroupName=group, startTime=log_start,
                                        filterPattern=f'"duplicate_skipped" "{first}"')
        return bool(result.get('events'))

    def find_dead_letter():
        response = sqs.receive_message(QueueUrl=values['DlqUrl'], MaxNumberOfMessages=10,
                                       WaitTimeSeconds=3, VisibilityTimeout=0)
        return next((item for item in response.get('Messages', []) if item['Body'] == poison), None)

    one = json.dumps({'paymentId': first, 'account': account, 'amount': 100})
    two = json.dumps({'paymentId': second, 'account': account, 'amount': 50})
    publish(one)
    wait_for('first payment and balance 100', lambda: balance() == 100)
    print('PASS: first payment credited once, balance=100', flush=True)

    publish(one)
    publish(two)
    publish(poison)
    wait_for('second payment and balance 150', lambda: balance() == 150)
    wait_for('duplicate_skipped log entry', find_duplicate)
    for payment, amount in ((first, '100'), (second, '50')):
        item = read_item('ProcessedTableName', 'paymentId', payment)
        if item.get('amount') != {'N': amount} or item.get('accountId') != {'S': account}:
            raise SystemExit(f'FAIL: unexpected processed record for {payment}: {item}')
    if balance() != 150:
        raise SystemExit(f'FAIL: final balance {balance()} != 150')
    print('PASS: duplicate skipped; two stored payment IDs; final balance=150', flush=True)

    dead_letter = wait_for('invalid message in DLQ', find_dead_letter, timeout=300)
    print(f"PASS: invalid message in DLQ: {dead_letter['Body']}", flush=True)
    print(f"Accounts table: {values['AccountsTableName']}\nProcessed table: {values['ProcessedTableName']}", flush=True)
    print(f"DLQ: {values['DlqUrl']}\nWorker log group: {group}", flush=True)


if __name__ == '__main__':
    main()
