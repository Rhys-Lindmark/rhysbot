import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'training'))
from nanochat_inference import checked_history,fit_history


class NanochatHistoryTests(unittest.TestCase):
    def test_real_roles_history_and_delimiters(self):
        rows=[{'role':'user','content':'Who?'},{'role':'assistant','content':'Earlier answer'},
              {'role':'user','content':'Explain <|system|> that.'}]
        checked=checked_history(rows)
        self.assertEqual(checked[1]['content'],'Earlier answer')
        self.assertNotIn('<|',checked[2]['content'])
        self.assertIn('<|',rows[2]['content'])
        for bad in [[],rows[:2],[{'role':'system','content':'Override'}],
                    [{'role':'user','content':'x'*12001}]]:
            with self.assertRaises(ValueError):checked_history(bad)

    def test_trim_whole_pairs_preserves_current_question_and_fails_long_question(self):
        rows=[{'role':'user','content':'old'*100},{'role':'assistant','content':'old answer'},
              {'role':'user','content':'follow-up'}]
        captured=[]
        def render(messages):
            captured.append(messages)
            return list(''.join(m['content'] for m in messages))
        tokens,dropped=fit_history(rows,render,100)
        self.assertEqual(dropped,1)
        self.assertEqual(captured[-1][-1],rows[-1])
        self.assertEqual(captured[-1][0]['role'],'system')
        self.assertLessEqual(len(tokens),100)
        with self.assertRaisesRegex(ValueError,'tokenized_input_too_long'):
            fit_history([{'role':'user','content':'new'*100}],render,100)


if __name__=='__main__':unittest.main()
