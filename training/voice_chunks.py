"""Bounded sentence/word units for early audio; no model or network imports."""
import re

def speech_chunks(text,first_limit=100,limit=180):
 words=re.findall(r'\S+',text);chunks=[];current=''
 for word in words:
  cap=first_limit if not chunks else limit
  if current and len(current)+1+len(word)>cap:chunks.append(current);current=''
  if len(word)>240:raise ValueError('A speech word exceeds the supported length')
  current=(current+' '+word).strip()
  if re.search(r'[.!?][\"\u201d\u2019]*$',current) and len(current)>=40:chunks.append(current);current=''
 if current:chunks.append(current)
 if not chunks or len(chunks)>32:raise ValueError('Unsupported speech chunk count')
 return chunks
