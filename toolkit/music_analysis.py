"""Music energy/onset candidates and exact cut-clock mapping; never auditory approval."""
import sys
sys.dont_write_bytecode = True
import argparse, array, hashlib, json, math, statistics, subprocess
from fractions import Fraction
from pathlib import Path
from video_workflow import new_output, write_json, WorkflowError, local_tool

def energy_candidates(samples, rate, hop_ms=20):
    hop = max(1, round(rate * hop_ms / 1000))
    energy = [math.sqrt(sum(x*x for x in samples[i:i+hop])/len(samples[i:i+hop]))
              for i in range(0, len(samples), hop)]
    flux = [0.0] + [max(0.0, energy[i]-energy[i-1]) for i in range(1,len(energy))]
    threshold = statistics.median(flux) + 1.5*statistics.pstdev(flux) if flux else 0
    peaks=[]
    for i in range(1,len(flux)-1):
        if flux[i]>threshold and flux[i]>=flux[i-1] and flux[i]>=flux[i+1]:
            if peaks and (i-peaks[-1])*hop/rate < .12:
                if flux[i]>flux[peaks[-1]]: peaks[-1]=i
            else: peaks.append(i)
    lag_scores={}
    for bpm in range(60,201):
        lag=max(1,round(60*rate/(bpm*hop)))
        if lag>=len(flux): continue
        score=sum(flux[i]*flux[i-lag] for i in range(lag,len(flux)))
        lag_scores[lag]=score
    scores=[(score,60*rate/(lag*hop)) for lag,score in lag_scores.items()]
    scores.sort(reverse=True)
    return {'hop_seconds':hop/rate,
            'onset_candidates':[{'source_seconds':i*hop/rate,'strength':flux[i]} for i in peaks],
            'tempo_candidates':[{'bpm':round(bpm,3),'relative_score':score/scores[0][0] if scores and scores[0][0] else 0} for score,bpm in scores[:5]],
            'energy_windows':[{'source_seconds':i*hop/rate,'rms':v} for i,v in enumerate(energy)]}

def map_music_cues(cues, cuts, fps):
    fps=Fraction(str(fps))
    if fps<=0: raise WorkflowError('Explicit positive target fps required')
    result=[]
    for cut in cuts:
        a,b=Fraction(str(cut['source_in_seconds'])),Fraction(str(cut['source_out_seconds']))
        start=Fraction(str(cut['target_in_frame']))
        speed=Fraction(str(cut.get('speed',1)))
        if not 0<=a<b or start<0 or speed<=0: raise WorkflowError('Invalid music cut')
        for cue in cues:
            point=Fraction(str(cue['source_seconds']))
            if a<=point<b:
                target=start+(point-a)*fps/speed
                result.append({'source_seconds':float(point),'cut_id':cut['id'],
                    'target_frame_exact':str(target),'target_frame_nearest':round(target),
                    'rounding_policy':'nearest_ties_to_even','status':'candidate_not_listened'})
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('path',type=Path);p.add_argument('--ffmpeg');p.add_argument('--hop-ms',type=int,default=20)
    p.add_argument('--cuts',type=Path,help='JSON array: id/source_in_seconds/source_out_seconds/target_in_frame/speed')
    p.add_argument('--fps',help='Explicit rational target fps required with cuts')
    p.add_argument('--out',type=Path);p.add_argument('--no-write',action='store_true')
    args=p.parse_args()
    try:
        src=args.path.resolve()
        if not src.is_file() or not 5<=args.hop_ms<=100: raise WorkflowError('Missing music or hop outside 5..100ms')
        ff=local_tool(args.ffmpeg,'ffmpeg')
        process=subprocess.run([ff,'-hide_banner','-nostdin','-v','error','-i',str(src),'-vn','-ac','1','-ar','16000','-f','f32le','pipe:1'],capture_output=True)
        if process.returncode: raise WorkflowError(process.stderr.decode('utf-8','replace')[:2000])
        samples=array.array('f');samples.frombytes(process.stdout)
        if sys.byteorder!='little':samples.byteswap()
        if not samples:raise WorkflowError('No decoded audio')
        report=energy_candidates(samples,16000,args.hop_ms)
        with src.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
        with Path(__file__).open('rb') as f:tool_hash=hashlib.file_digest(f,'sha256').hexdigest()
        report.update(input_path=str(src),input_sha256=h,tool_sha256=tool_hash,decoded_duration_seconds=len(samples)/16000,
            method='mono16k RMS positive-delta peaks and autocorrelation; suggestions only',
            limitations=['RMS peaks need not be musical beats; vocals/half-time/double-time can mislead',
                         'Tempo may vary; do not force a global BPM or constant cut length',
                         'Final beat alignment and musical phrasing require actual export review and user listening'],
            human_listening_verified=False)
        if args.cuts:
            if args.fps is None:raise WorkflowError('Music cuts require --fps')
            cuts=json.loads(args.cuts.read_text(encoding='utf-8-sig'))
            report['mapped_cues']=map_music_cues(report['onset_candidates'],cuts,args.fps)
        if args.no_write:
            print(json.dumps(report,ensure_ascii=False));return
        if args.out is None:raise WorkflowError('Specify --out new audit directory or --no-write')
        protected=[src]+([args.cuts.resolve()] if args.cuts else [])
        output=new_output(args.out,extra_protected=protected)
        write_json(output/'MUSIC_ANALYSIS.json',report)
        print(json.dumps({'output':str(output),'onset_candidates':len(report['onset_candidates']),
                          'tempo_candidates':report['tempo_candidates'],'human_listening_verified':False}))
    except (WorkflowError,OSError,ValueError,KeyError) as error:
        p.exit(2,str(error)+'\n')
if __name__=='__main__':main()
