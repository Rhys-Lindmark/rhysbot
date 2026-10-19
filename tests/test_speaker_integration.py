"""Speaker release safety, explicit identities and canonical edition replacement."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from integrate_speaker_transcripts import sanitize_episode, corpus_chunks, integrate, validate_sidecar, validate_public_sidecars
from corpus_lib import digest,group_split

PINS={'asr':['org/asr','a'*40],'diarization':['org/diarization','b'*40],'voice':['org/voice','c'*40]}


def evidence(key='episode-a',youtube='abcdefghijk'):
    return {'schema_version':1,'episode':{'id':key,'title':'Public scientific discussion',
            'source_url':'https://soundcloud.com/the-rhys-show/'+key,
            'enclosure_url':'https://feeds.soundcloud.com/stream/'+key+'.mp3',
            'youtube_id':youtube,'youtube_url':'https://www.youtube.com/watch?v='+youtube if youtube else None,
            'published_at':'2020-01-01T00:00:00Z'},
            'audio_sha256':'d'*64,'audio_duration_seconds':100.0,'pins':{key:list(value) for key,value in PINS.items()},'rhys_speaker_id':'speaker_0',
            'word_count':100,'unknown_word_count':5,
            'voice_match_cosines':{'speaker_0':['PRIVATE_VOICEPRINT_MARKER']},
            'identity_anchor':{'path':'/Users/private/DO_NOT_PUBLISH'},
            'words':[{'word':'PRIVATE_WORD_ARRAY_MARKER','probability':.98}],
            'speaker_segments':[{'PRIVATE_SEGMENT_MARKER':True}],
            'turns':[{'start':0.,'end':3.,'speaker_id':'speaker_0','speaker':'Invented display name',
                      'text':'Scientific knowledge grows through careful observation and public discussion.'},
                     {'start':3.,'end':6.,'speaker_id':'speaker_1','speaker':'Rhys Lindmark',
                      'text':'Evidence matters when people evaluate competing explanations of nature.'},
                     {'start':6.,'end':7.,'speaker_id':'unknown','text':'An uncertain overlap remains explicitly uncertain.'}]}


def old_row(key,kind,url,group='original-episode-group'):
    text='Unique historical approved content for '+key+' remains part of this release.'
    row={'id':key,'document_id':key,'source_type':kind,'collection':{'anki':'anki','hee_transcript':'hee'}.get(kind,'podcasts_youtube'),
         'source_url':url,'source_group_id':group,'split_group_id':group,'split':group_split(group),
         'title':'Historical discussion','published_at':None,'authorship':'original-authorship',
         'text':text,'content_sha256':digest(text),'privacy_status':'public_source_automated_screen','acquisition':{'method':'original'}}
    if kind=='hee_transcript':
        row['authorship']='rhys_lindmark_narration_owner_confirmed'
        row['speaker_attribution']={'basis':'owner confirmation'}
    return row


def fixture(root,episodes):
    public=root/'public';public.mkdir();incoming=root/'private-input';incoming.mkdir();work=root/'private-work'
    for item in episodes:(incoming/(item['episode']['id']+'.json')).write_text(json.dumps(item))
    first=episodes[0]['episode']
    rows=[old_row('anki-a','anki',None,'anki-group'),old_row('hee-a','hee_transcript','https://www.youtube.com/watch?v=hee00000000','hee-group'),
          old_row('rss-old','podcast_transcript',first['source_url']),
          old_row('yt-matched','youtube_transcript',first['youtube_url']),
          old_row('yt-only','youtube_transcript','https://www.youtube.com/watch?v=videoonly01','video-only-group')]
    rows.sort(key=lambda row:row['id'])
    (public/'corpus.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (public/'anki-cards.jsonl').write_text(json.dumps({**rows[0],'corpus_record_id':rows[0]['id']})+'\n')
    stats={'retained_anki_cards_before_text_deduplication':1,'by_source':{'anki':1},'frozen_model_training_input':{'source_corpus_sha256':'FROZEN'},
           'tokens':123,'tokens_by_source':{'old':123},'token_count_corpus_sha256':'old'}
    (public/'stats.json').write_text(json.dumps(stats))
    return public,incoming,work,rows


class SpeakerIntegrationTests(unittest.TestCase):
    def test_whitelist_never_publishes_private_identity_inputs_or_guessed_names(self):
        clean=sanitize_episode(evidence())
        self.assertEqual([turn['speaker'] for turn in clean['turns']],['Rhys Lindmark','Guest Speaker 2','Unknown / overlap'])
        self.assertEqual(clean['turns'][1]['speaker_id'],'speaker_1')
        serialized=json.dumps(clean)
        for secret in ['PRIVATE_VOICEPRINT_MARKER','PRIVATE_WORD_ARRAY_MARKER','PRIVATE_SEGMENT_MARKER','DO_NOT_PUBLISH','voice_match_cosines','identity_anchor','"probability":']:
            self.assertNotIn(secret,serialized)
        anonymous=evidence();anonymous.pop('rhys_speaker_id')
        self.assertEqual(sanitize_episode(anonymous)['turns'][0]['speaker'],'Guest Speaker 1')

    def test_public_validator_rejects_raw_private_keys_and_unbound_labels(self):
        sidecar=sanitize_episode(evidence())
        validate_sidecar(sidecar)
        for mutation in [lambda value:value.update(voice_match_cosines=[.99]),
                         lambda value:value['turns'][1].update(speaker='Named guest inferred from title'),
                         lambda value:value['turns'][0].update(text='Contact private@example.test'),
                         lambda value:value['turn_counts'].update(total=999),
                         lambda value:value['pins'].update(private_voiceprints=[.99]),
                         lambda value:value['word_alignment'].update(cosines=[.99])]:
            changed=json.loads(json.dumps(sidecar));mutation(changed)
            with self.assertRaises(ValueError):validate_sidecar(changed)

    def test_privacy_withholds_turn_text_and_boundary_split_secrets(self):
        item=evidence()
        item['turns'][1]['text']='Contact private-person@example.test for more information.'
        clean=sanitize_episode(item)
        self.assertIsNone(clean['turns'][1]['text'])
        self.assertNotIn('private-person',json.dumps(clean))
        self.assertEqual(clean['turn_counts']['withheld'],1)
        boundary=evidence()
        boundary['turns'][0]['text']='api'
        boundary['turns'][1]['text']='key'
        output=sanitize_episode(boundary)
        self.assertIsNone(output['turns'][0]['text']);self.assertIsNone(output['turns'][1]['text'])

    def test_invalid_times_ids_urls_and_pins_fail_closed(self):
        for mutate in [lambda x:x['turns'][0].update(start=-1),lambda x:x['turns'][2].update(start=1),
                       lambda x:x.update(rhys_speaker_id='Barbara'),
                       lambda x:x['episode'].update(enclosure_url='https://feeds.soundcloud.com/stream/a?token=private'),
                       lambda x:x['pins'].update(asr=['org/asr','main'])]:
            item=evidence();mutate(item)
            with self.assertRaises(ValueError):sanitize_episode(item)

    def test_chunks_remain_bounded_labeled_and_linked_to_actual_turn_bounds(self):
        item=evidence();item['turns'][0]['text']=' '.join(['knowledge']*710)
        clean=sanitize_episode(item);rows=list(corpus_chunks(clean))
        self.assertGreaterEqual(len(rows),3)
        for row in rows:
            self.assertLessEqual(len(__import__('re').sub(r'\[[^\]\n]+\] ','',row['text']).split()),350)
            self.assertIn('[',row['text'])
            self.assertGreaterEqual(row['turn_index_start'],0)
            self.assertLess(row['turn_index_end'],len(clean['turns']))

    def test_plan_is_read_only_and_apply_replaces_only_explicit_episode_editions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);public,incoming,work,old=fixture(root,[evidence()])
            before={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in public.iterdir()}
            plan=integrate(incoming,public,work,False)
            self.assertEqual(plan['replaced_excerpts_by_source'],{'podcast_transcript':1,'youtube_transcript':1})
            self.assertEqual(before,{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in public.iterdir()})
            self.assertFalse((public/'speaker-transcripts').exists())
            report=integrate(incoming,public,work,True)
            rows=[json.loads(line) for line in (public/'corpus.jsonl').read_text().splitlines()]
            ids={row['id'] for row in rows}
            self.assertIn('yt-only',ids);self.assertNotIn('yt-matched',ids);self.assertNotIn('rss-old',ids)
            for key in ['anki-a','hee-a','yt-only']:
                self.assertEqual(next(row for row in rows if row['id']==key),next(row for row in old if row['id']==key))
            canonical=next(row for row in rows if row['id'].startswith('podcast-speaker:'))
            self.assertEqual(canonical['split_group_id'],'original-episode-group')
            self.assertEqual(canonical['authorship'],'mixed_or_unverified_speakers')
            self.assertTrue(canonical['speaker_transcript_url'].startswith('https://raw.githubusercontent.com/'))
            self.assertEqual(report['matched_old_corpus_episodes'],1)
            self.assertEqual(report['episodes_processed'],1)
            self.assertEqual(report['episodes_with_rhys_match'],1)
            self.assertEqual(report['episodes_anonymous_or_ambiguous'],0)
            self.assertEqual(report['screened_turn_count'],3)
            self.assertAlmostEqual(report['unknown_words_fraction'],.05)
            sidecar=json.loads((public/'speaker-transcripts/episode-a.json').read_text())
            self.assertEqual(sidecar['audio_sha256'],'d'*64)
            self.assertFalse(sidecar['voiceprints_published'])
            self.assertEqual(validate_public_sidecars(public)['public_sidecars'],1)
            self.assertEqual(validate_public_sidecars(public)['linked_corpus_excerpts'],1)
            stats=json.loads((public/'stats.json').read_text())
            self.assertEqual(stats['frozen_model_training_input'],{'source_corpus_sha256':'FROZEN'})
            self.assertIsNone(stats['tokens']);self.assertNotIn('tokens_by_source',stats)
            self.assertEqual(stats['corpus_jsonl_bytes'],(public/'corpus.jsonl').stat().st_size)
            self.assertEqual(report['output_corpus_jsonl_bytes'],stats['corpus_jsonl_bytes'])
            self.assertEqual(report['output_text_utf8_bytes'],stats['text_utf8_bytes'])
            self.assertEqual(report['output_excerpts'],len(rows))

    def test_audio_only_and_unscreenable_episode_keep_precise_replacement_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=evidence();b=evidence('audio-only',None)
            b['turns'][0]['text']='Another distinct scientific interview recorded only in the original public audio feed.'
            public,incoming,work,old=fixture(root,[a,b])
            audio=old_row('audio-old','podcast_transcript',b['episode']['source_url'],'audio-only-group')
            original=sorted([*old,audio],key=lambda row:row['id'])
            (public/'corpus.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in original))
            result=integrate(incoming,public,work,True)
            rows=[json.loads(line) for line in (public/'corpus.jsonl').read_text().splitlines()]
            self.assertNotIn('audio-old',{row['id'] for row in rows})
            self.assertEqual(result['episodes_with_youtube_alias'],1)
            audio_sidecar=json.loads((public/'speaker-transcripts/audio-only.json').read_text())
            self.assertIsNone(audio_sidecar['youtube_url'])
            self.assertEqual(next(row for row in rows if row['source_url']==b['episode']['source_url'])['split_group_id'],'audio-only-group')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=evidence();b=evidence('all-withheld','lmnopqrstuv')
            for turn in b['turns']:turn['text']='Contact private-person@example.test for information.'
            public,incoming,work,old=fixture(root,[a,b])
            unrelated=old_row('unprocessed-alias','youtube_transcript',b['episode']['youtube_url'],'untouched-group')
            (public/'corpus.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in sorted([*old,unrelated],key=lambda row:row['id'])))
            result=integrate(incoming,public,work,True)
            rows=[json.loads(line) for line in (public/'corpus.jsonl').read_text().splitlines()]
            self.assertIn('unprocessed-alias',{row['id'] for row in rows})
            self.assertEqual(result['gaps_aggregate']['no_publishable_corpus_text'],1)
            self.assertFalse((public/'speaker-transcripts/all-withheld.json').exists())

    def test_youtube_audio_is_canonical_video_only_and_never_erases_rss_by_group(self):
        item=evidence('podcast-'+'e'*20)
        item['episode'].update(audio_source_kind='youtube_audio',enclosure_url=None,
                               source_url=item['episode']['youtube_url'])
        clean=sanitize_episode(item);validate_sidecar(clean)
        self.assertIsNone(clean['rss_feed_url']);self.assertIsNone(clean['rss_enclosure_url'])
        self.assertEqual(clean['audio_source_kind'],'youtube_audio')
        for mutate in [lambda value:value['episode'].update(source_url='https://example.com/signed?secret=value'),
                       lambda value:value['episode'].update(youtube_url=value['episode']['youtube_url']+'&token=secret'),
                       lambda value:value['episode'].update(enclosure_url='https://feeds.soundcloud.com/stream/a.mp3'),
                       lambda value:value['episode'].update(youtube_id=None)]:
            changed=json.loads(json.dumps(item));mutate(changed)
            with self.assertRaises(ValueError):sanitize_episode(changed)
        with tempfile.TemporaryDirectory() as directory:
            public,incoming,work,old=fixture(Path(directory),[item])
            rss=next(row for row in old if row['id']=='rss-old')
            rss['source_url']='https://soundcloud.com/the-rhys-show/different-rss-edition'
            rss['source_group_id']=item['episode']['youtube_url']
            alias_only=old_row('yt-group-only','youtube_transcript','https://www.youtube.com/watch?v=otherclip01')
            alias_only['source_group_id']=item['episode']['youtube_url']
            old=sorted([*old,alias_only],key=lambda row:row['id'])
            (public/'corpus.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in old))
            report=integrate(incoming,public,work,True)
            rows=[json.loads(line) for line in (public/'corpus.jsonl').read_text().splitlines()]
            ids={row['id'] for row in rows}
            self.assertIn('rss-old',ids);self.assertIn('yt-group-only',ids);self.assertNotIn('yt-matched',ids)
            self.assertEqual(report['replaced_excerpts_by_source'],{'youtube_transcript':1})
            self.assertEqual(report['youtube_audio_episodes'],1);self.assertEqual(report['rss_audio_episodes'],0)
            canonical=next(row for row in rows if row['id'].startswith('podcast-speaker:'))
            self.assertEqual(canonical['source_type'],'youtube_transcript')
            self.assertEqual(canonical['acquisition']['media'],'youtube_audio')
            self.assertEqual(canonical['source_aliases'],[item['episode']['youtube_url']])
            self.assertEqual(validate_public_sidecars(public)['public_sidecars'],1)
            for key in ['anki-a','hee-a','yt-only','rss-old','yt-group-only']:
                self.assertEqual(next(row for row in rows if row['id']==key),next(row for row in old if row['id']==key))

    def test_public_validator_binds_corpus_text_hash_times_and_chunk_index(self):
        with tempfile.TemporaryDirectory() as directory:
            public,incoming,work,_=fixture(Path(directory),[evidence()])
            integrate(incoming,public,work,True)
            original=(public/'corpus.jsonl').read_text()
            for mutate in [lambda row:row.update(text='A different ordinary public scientific statement.',content_sha256=digest('A different ordinary public scientific statement.')),
                           lambda row:row.update(content_sha256='0'*64),
                           lambda row:row.update(time_start_seconds=1),
                           lambda row:row.update(time_end_seconds=8),
                           lambda row:row.update(turn_index_start=1),
                           lambda row:row.update(turn_index_end=1),
                           lambda row:row.update(id=row['id'][:-5]+'99999')]:
                rows=[json.loads(line) for line in original.splitlines()]
                mutate(next(row for row in rows if row.get('speaker_transcript')))
                (public/'corpus.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
                with self.assertRaises(ValueError):validate_public_sidecars(public)
            # Deduplication may legitimately omit a generated chunk: only rows
            # that are represented must match; sidecars remain independently valid.
            retained=[json.loads(line) for line in original.splitlines() if not json.loads(line).get('speaker_transcript')]
            (public/'corpus.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in retained))
            self.assertEqual(validate_public_sidecars(public)['linked_corpus_excerpts'],0)

    def test_oversized_staged_output_cannot_partially_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            public,incoming,work,_=fixture(Path(directory),[evidence()])
            before={path.name:path.read_bytes() for path in public.iterdir()}
            with patch('integrate_speaker_transcripts.MAX_CORPUS_BYTES',len(before['corpus.jsonl'])+1):
                with self.assertRaises(ValueError):integrate(incoming,public,work,True)
            self.assertEqual(before,{path.name:path.read_bytes() for path in public.iterdir()})
            self.assertFalse((public/'speaker-transcripts').exists())

    def test_duplicate_aliases_and_duplicate_short_text_never_leak_splits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            a=evidence('episode-a');b=evidence('episode-b','lmnopqrstuv')
            a['turns']=a['turns'][:1];b['turns']=b['turns'][:1]
            public,incoming,work,_=fixture(root,[a,b])
            result=integrate(incoming,public,work,True)
            rows=[json.loads(line) for line in (public/'corpus.jsonl').read_text().splitlines()]
            canonical=[row for row in rows if row['id'].startswith('podcast-speaker:')]
            self.assertEqual(len(canonical),1)
            self.assertEqual(result['exact_new_excerpt_duplicates_removed'],1)
            self.assertIn(b['episode']['source_url'],canonical[0]['duplicate_source_urls'])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=evidence('episode-a');b=evidence('episode-b')
            public,incoming,work,_=fixture(root,[a,b])
            original=(public/'corpus.jsonl').read_bytes()
            with self.assertRaises(ValueError):integrate(incoming,public,work,True)
            self.assertEqual((public/'corpus.jsonl').read_bytes(),original)


if __name__=='__main__':unittest.main()
