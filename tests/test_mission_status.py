"""Sender recovery regression checks; RF/SPI calls are mocked, packet parsing is real."""
import contextlib
import importlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Only the unavailable SPI module is substituted; no physical radio is instantiated.
with patch.dict(sys.modules, {'spidev': MagicMock()}):
    mission = importlib.import_module('hardware.pi.mission_send')


class MissionStatusTests(unittest.TestCase):
    def run_mission(self, sends, requested, final=None):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'source.jpg'
            source.write_bytes(b'source')
            def prepare(original, destination, quality):
                destination.write_bytes(b'J'*400)
                return 1
            output = io.StringIO()
            with patch.object(mission, 'find_classifier', return_value=source), \
                 patch.object(mission, 'run_classifier', return_value={'class':'CLOUDY','confidence':.4151}), \
                 patch.object(mission, 'prepare_jpeg', side_effect=prepare), \
                 patch.object(mission, 'make_metadata', side_effect=lambda a,b,c,d,e,f: d), \
                 patch.object(mission, 'send_metadata', side_effect=lambda radio, meta, send: send(radio, 0, b'meta')), \
                 patch.object(mission, 'SX1278') as radio, \
                 patch.object(mission, 'send_chunk', side_effect=sends) as sender, \
                 patch.object(mission, 'request_image_status', side_effect=requested) as requester, \
                 patch.object(mission, 'wait_for_image_status', return_value=final) as waiter, \
                 patch.object(sys, 'argv', ['mission_send.py',str(source),'--image-id','TEST']), \
                 contextlib.redirect_stdout(output):
                result = mission.main()
                radio.return_value.close.assert_called_once()
                return result, output.getvalue(), sender.call_count, requester.call_count, waiter.call_count

    def complete(self, total=2):
        return {'complete':True,'failed':False,'missing':[],'total':total,'received':total}

    def test_lost_final_ack_complete_status_succeeds_without_second_status_wait(self):
        result, output, sends, requests, waits = self.run_mission([True,True,True,False],[self.complete()])
        self.assertEqual(result,0)
        self.assertIn('lost ACK recovered by status',output)
        self.assertIn('2/2 image chunks receiver-verified',output)
        self.assertEqual((sends,requests,waits),(4,1,0))

    def test_missing_intermediate_ack_with_receipt_does_not_send_future_chunks_early(self):
        status = {'complete':False,'failed':False,'missing':[1],'total':2,'received':1}
        result, _, sends, requests, waits = self.run_mission([True,True,False,True],[status],self.complete())
        self.assertEqual(result,0)
        self.assertEqual((sends,requests,waits),(4,1,1))

    def test_missing_final_chunk_retried_and_complete_status_recovers_second_lost_ack(self):
        status = {'complete':False,'failed':False,'missing':[1],'total':2,'received':1}
        result, _, sends, requests, waits = self.run_mission([True,True,True,False,False],[status,self.complete()])
        self.assertEqual(result,0)
        self.assertEqual((sends,requests,waits),(5,2,0))

    def test_lost_unsolicited_completion_is_queried(self):
        result, _, sends, requests, waits = self.run_mission([True]*4,[self.complete()])
        self.assertEqual(result,0)
        self.assertEqual((sends,requests,waits),(4,1,1))

    def test_wrong_total_failure_and_missing_receipt_never_succeed(self):
        for status in (self.complete(3), {'complete':False,'failed':True,'missing':[],'total':2}, None):
            with self.subTest(status=status):
                result, _, _, _, _ = self.run_mission([True,True,True,False],[status])
                self.assertEqual(result,1)

    def test_status_parser_rejects_truncated_wrong_id_and_inconsistent_bitmap(self):
        identity = 0x554e3a0f
        header = bytes([0xC2,1]) + identity.to_bytes(4,'big') + (156).to_bytes(2,'big')*2
        bitmap = b'\xff'*19 + b'\x0f'
        status = mission.decode_image_status(header+bitmap, identity)
        self.assertTrue(status['complete'])
        self.assertEqual(status['missing'],[])
        for payload in (header, header+bitmap[:-1], header+b'\x00'*20,
                        bytes([0xC2,3])+header[2:]+bitmap):
            self.assertIsNone(mission.decode_image_status(payload,identity))
        self.assertIsNone(mission.decode_image_status(header+bitmap,identity+1))


if __name__ == '__main__':
    unittest.main()
