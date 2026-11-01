import unittest, tempfile, sys
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from support_service import SupportService, answer_help

class SupportTests(unittest.TestCase):
    def test_reports_are_local_and_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            service=SupportService(temp)
            incident=service.record('tools','HTTP_500','/api/build-reel?token=abc','apiKey: private-value sk-testsecret https://example.com/?password=xyz')
            self.assertFalse(incident['sent']);self.assertEqual(incident['status'],'saved_locally')
            report=service.recent()[0]
            self.assertEqual(report['section'],'tools');self.assertEqual(report['code'],'HTTP_500')
            self.assertNotIn('private-value',report['description']);self.assertNotIn('testsecret',report['description'])
            self.assertNotIn('xyz',report['description'])
            self.assertEqual(SupportService(temp).recent()[0]['id'],incident['id'])

    def test_duplicate_errors_do_not_create_report_flood(self):
        with tempfile.TemporaryDirectory() as temp:
            service=SupportService(temp)
            first=service.record('tools','HTTP_500','/api/build-reel')
            second=service.record('tools','HTTP_500','/api/build-reel')
            self.assertEqual(first['id'],second['id']);self.assertEqual(len(service.recent()),1)
            self.assertEqual(service.recent()[0]['occurrences'],2)

    def test_help_cannot_execute_actions_or_claim_email_integration(self):
        seen=[]
        def mock(payload,key):
            seen.append(payload)
            return {'output':[{'type':'message','content':[{'type':'output_text','text':'Te guío con el reel.'}]}]}
        with patch('agent_runtime.request_response',mock):
            result=answer_help({'message':'Cómo armo un reel','section':'tools'}, {'aiConfig':{'apiKey':'fixture'}})
        self.assertEqual(result,'Te guío con el reel.')
        self.assertNotIn('tools',seen[0]);self.assertFalse(seen[0]['store'])
        self.assertIn('NO hay envío a correo',seen[0]['instructions'])
        self.assertIn('Herramientas',seen[0]['instructions'])
        self.assertNotIn('fixture',str(seen[0]))

    def test_missing_key_and_invalid_question(self):
        self.assertIn('falta configurar',answer_help({'message':'Hola'},{}))
        with self.assertRaises(ValueError): answer_help({'message':''},{})
        with self.assertRaises(ValueError): answer_help({'message':'a'*2001},{})

if __name__=='__main__':unittest.main(verbosity=2)
