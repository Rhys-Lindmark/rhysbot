import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/'training'))
from voice_chunks import speech_chunks
class SpeechChunksTests(unittest.TestCase):
 def test_every_word_preserved_with_early_short_opening_and_bounded_remaining_parts(self):
  text='A new sentence with ordinary words and punctuation. '+('We should hear this while the rest of the reply is still generating. '*25)
  parts=speech_chunks(text)
  self.assertEqual(' '.join(parts).split(),text.split());self.assertLessEqual(len(parts[0]),100);self.assertTrue(all(len(p)<=180 for p in parts[1:]));self.assertLessEqual(len(parts),32)
 def test_sentence_boundaries_unicode_and_unpronounceable_blob(self):
  self.assertEqual(speech_chunks('Hello there. A second sentence.'),['Hello there. A second sentence.'])
  self.assertEqual(' '.join(speech_chunks('Café is a word. '*20)).split(),('Café is a word. '*20).split())
  for t in ['', 'z'*241]:
   with self.assertRaises(ValueError):speech_chunks(t)
