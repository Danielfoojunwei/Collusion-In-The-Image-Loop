"""Prospectively repriced GLM/Qwen broker for the single-factor pilot."""
import base64
import io
from PIL import Image

from next_batch.oversight_v17.openrouter import Client, MODEL, payload
from next_batch.oversight_v17.finance import canonical, FundingHold
from next_batch.oversight_v17.events import IntegrityError


def png_info(raw):
    if not isinstance(raw,bytes) or len(raw)>12*1024*1024 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise IntegrityError('Bounded genuine PNG required')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format!='PNG' or max(im.size)>4096 or min(im.size)<32 or getattr(im,'n_frames',1)!=1:
            raise IntegrityError('Image dimensions/frame count invalid')
        size=im.size;im.verify()
    return size


def block(raw):
    png_info(raw)
    return {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw).decode(),'detail':'high'}}


class Provider(Client):
    def chat(self,request_id,messages,*,tool,max_tokens=8192,kind='actor'):
        if max_tokens!=8192:raise FundingHold('Fixed pilot output limit changed')
        text=len(canonical(tool).encode())+2048+128*len(messages);images=0
        for message in messages:
            content=message.get('content')
            if isinstance(content,str):text+=len(content.encode())
            elif isinstance(content,list):
                for part in content:
                    if part.get('type')=='text':text+=len(part['text'].encode())
                    elif part.get('type')=='image_url':
                        url=part['image_url']['url']
                        if not url.startswith('data:image/png;base64,'):raise IntegrityError('Actual image bytes required')
                        png_info(base64.b64decode(url.split(',',1)[1],validate=True));images+=1
                    else:raise IntegrityError('Unknown input block')
            else:raise IntegrityError('Explicit bounded message required')
        if text>16384 or images>4:raise FundingHold('Pilot context envelope exceeded; no truncation')
        body=payload(messages,max_tokens,tool)
        body['provider']['max_price']={'prompt':.15,'completion':.25,'request':0}
        reserve=(text+images*8192)*.15e-6+2*max_tokens*.25e-6+.002
        return self._post('/chat/completions',request_id,canonical(body).encode(),kind,reserve,
            {MODEL,'z-ai/glm-5.3-flash-20260826'},{'OpenInference'})
