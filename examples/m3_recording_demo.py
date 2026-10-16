"""Generate explicitly synthetic M3 evidence; never accesses a game or device."""

import argparse
import base64
import hashlib
import json
from pathlib import Path

PNG={
    'before':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGMQ0QgAAADgAI3uVJhMAAAAAElFTkSuQmCC',
    'after':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGPQCFgAAAG8ARlfd8kpAAAAAElFTkSuQmCC',
}
MARKER='AI_TEST_OBSERVATION_JSON:'


def create_demo(output_dir,scenario='pass'):
    if scenario not in {'pass','wrong-debit','missing-event'}:
        raise ValueError('unsupported synthetic scenario')
    fixture=Path(__file__).resolve().parents[1]/'fixtures'/'m3-recording.example.json'
    data=json.loads(fixture.read_text(encoding='utf-8'))
    if scenario!='pass':
        prefix,payload=data['after']['line'].split(MARKER)
        after=json.loads(payload)
        if scenario=='wrong-debit':
            after['wallet']['balance']='92.45'
            after['ledger']['events'][-1].update(delta='-9.05',balance_after='92.45')
        else:
            after['ledger']['first_seq']=2
            after['ledger']['events']=after['ledger']['events'][1:]
        data['after']['line']=prefix+MARKER+json.dumps(after)
    images={name:base64.b64decode(value,validate=True) for name,value in PNG.items()}
    for name,image in images.items():
        evidence=data[name]['capture']['screenshot']
        if evidence['sha256']!=hashlib.sha256(image).hexdigest() or evidence['bytes']!=len(image):
            raise ValueError('synthetic fixture PNG metadata does not match embedded image')
    output=Path(output_dir).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('choose an empty output directory; existing artifacts are preserved')
    output.mkdir(parents=True,exist_ok=True)
    for name,image in images.items():
        with (output/(name+'.png')).open('xb') as handle:
            handle.write(image)
    path=output/'recording.json'
    with path.open('x',encoding='utf-8') as handle:
        json.dump(data,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.write('\n')
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--scenario',choices=['pass','wrong-debit','missing-event'],default='pass')
    args=parser.parse_args()
    try:
        path=create_demo(args.output_dir,args.scenario)
    except (ValueError,OSError) as error:
        parser.exit(2,str(error)+'\n')
    print(json.dumps({'status':'PASS','evidence_mode':'synthetic','recording':str(path),'scenario':args.scenario}))


if __name__=='__main__':
    main()
