import sys
import unittest
sys.path.insert(0,'/app')
from asr_candidates import apply_consensus

class CandidateTests(unittest.TestCase):
    def test_requires_three_overlapping_agreements(self):
        rows=[{'id':0,'start':10,'end':11,'source':'old'}]
        candidates=[{'requested_language':language,'segments':[{'start':9.9,'end':11.1,'text':'New text.'}]} for language in ('auto','en','fr')]
        experiment={'windows':[{'start':9,'end':12,'candidates':candidates}]}
        result=apply_consensus(rows,experiment)
        self.assertEqual(result[0]['source'],'New text.')
        self.assertEqual(result[0]['source_before'],'old')
        self.assertEqual(rows[0]['source'],'old')
        candidates[1]['segments'][0]['text']='Different'
        self.assertEqual(apply_consensus(rows,experiment),rows)
        candidates[1]['segments']=[]
        self.assertEqual(apply_consensus(rows,experiment),rows)
