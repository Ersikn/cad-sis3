import unittest
from unittest.mock import Mock

from worker import apply_payment, handle_message, parse_payment


class Canceled(Exception):
    pass


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.exceptions.TransactionCanceledException = Canceled
        self.sqs = Mock()
        self.message = {
            'MessageId': 'test-event', 'ReceiptHandle': 'receipt',
            'Body': '{"paymentId":"pay-1","account":"acc-1","amount":100}',
        }

    def handle(self):
        handle_message(self.sqs, self.db, 'queue-url', 'processed', 'accounts', self.message)

    def test_records_and_credits_in_one_transaction(self):
        self.handle()
        items = self.db.transact_write_items.call_args.kwargs['TransactItems']
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['Put']['ConditionExpression'], 'attribute_not_exists(paymentId)')
        self.assertEqual(items[1]['Update']['ExpressionAttributeValues'], {':amount': {'N': '100'}})
        self.sqs.delete_message.assert_called_once_with(QueueUrl='queue-url', ReceiptHandle='receipt')

    def test_duplicate_is_acknowledged(self):
        self.db.transact_write_items.side_effect = Canceled()
        self.db.get_item.return_value = {'Item': {'accountId': {'S': 'acc-1'}, 'amount': {'N': '100'}}}
        self.handle()
        self.assertTrue(self.db.get_item.call_args.kwargs['ConsistentRead'])
        self.sqs.delete_message.assert_called_once()

    def test_invalid_message_is_left_for_redrive(self):
        self.message['Body'] = 'not-json'
        self.handle()
        self.db.transact_write_items.assert_not_called()
        self.sqs.delete_message.assert_not_called()

    def test_retryable_transaction_error_is_not_acknowledged(self):
        self.db.transact_write_items.side_effect = Canceled()
        self.db.get_item.return_value = {}
        with self.assertRaises(Canceled):
            self.handle()
        self.sqs.delete_message.assert_not_called()

    def test_conflicting_payment_is_not_acknowledged(self):
        self.db.transact_write_items.side_effect = Canceled()
        self.db.get_item.return_value = {'Item': {'accountId': {'S': 'other'}, 'amount': {'N': '100'}}}
        self.handle()
        self.sqs.delete_message.assert_not_called()

    def test_boolean_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_payment('{"paymentId":"pay-1","account":"acc-1","amount":true}')
