"""Local deterministic multimodal responses; never use for paid/live evaluation."""
import base64
from http.server import BaseHTTPRequestHandler
from io import BytesIO
import json
import re


class MediaProvider(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    requests=[]
    characters=False
    duplicate_candidates=False
    signed_urls=False
    images={}
    content='雨继续下着。门廊里的灯亮了。'
    def log_message(self,*args): pass
    def do_POST(self):
        from story_benchmark.gateway import parse_payload
        body,_=parse_payload(self.rfile.read(int(self.headers['Content-Length'])),self.headers.get('Content-Type','application/json'))
        self.requests.append({'path':self.path,'body':body})
        if '/images/' in self.path:
            from PIL import Image,ImageDraw
            image=Image.new('RGB',(1536,1024),(85,103,125))
            draw=ImageDraw.Draw(image);draw.rectangle((170,100,700,900),fill=(230,224,206))
            if 'exactly one character' in body.get('prompt','').lower():
                image=Image.new('RGB',(1024,1536),'white');draw=ImageDraw.Draw(image)
                draw.ellipse((330,130,690,480),fill=(192,148,117));draw.rectangle((290,470,730,1250),fill=(45,66,101))
            # Optional equal-byte candidates expose invalid hash-only linkage.
            if not self.duplicate_candidates: image.putpixel((0,0),(len(self.requests)%255,11,37))
            stream=BytesIO();image.save(stream,format='PNG')
            response={'created':0,'data':[{'b64_json':base64.b64encode(stream.getvalue()).decode()}],
                'usage':{'input_tokens':12,'output_tokens':34,'total_tokens':46}}
            if self.duplicate_candidates: response['data']*=2
            if self.signed_urls:
                image_path='/fixture-image-'+str(len(self.requests))+'.png'
                self.images[image_path]=stream.getvalue()
                url=f'http://127.0.0.1:{self.server.server_port}{image_path}?Policy=fixture-private-policy&X-Amz-Signature=fixture-private-signature'
                response['data']=[{'url':url} for _ in response['data']]
        else:
            role=body['messages'][0]['content'];user=body['messages'][-1]['content']
            if '小说策划师' in role:
                result={'worldview':'测试世界','characters':([{'character_id':'fixture-lin','name':'林澈',
                    'appearance':'A young adult man with short dark hair, brown eyes and a navy raincoat.'}] if self.characters else []),
                    'character_relations':'','main_conflict':'等待','emotional_line':'','style_rules':'简体中文',
                    'ending_constraints':'','forbidden_points':[],'writing_notes':[]}
            elif '大纲策划师' in role:
                result={'chapters':[{'chapter_index':i,'title':f'第{i}章','summary':'等待雨停。','scene':'门廊'} for i in range(1,14)]}
            elif 'Script IR' in role:
                result={'segments':[{'text':'雨继续下着。','kind':'narration','scene_key':'s1',
                    'stage_events':([{'type':'enter','character_id':'fixture-lin'}] if self.characters else [])},
                    {'text':'门廊里的灯亮了。','kind':'narration','scene_key':'s1','keyframe':{'required':True,'prompt':'An empty porch illuminated by a lamp.'}}],
                    'scenes':[{'scene_key':'s1','title':'门廊','location':'门廊'}]}
            elif '小说作家' in role:
                match=re.search(r'第\s*(\d+)/',user)
                result={'chapter_index':int(match.group(1)) if match else 2,'title':'门廊','content':self.content}
            elif '互动叙事分支规划器' in role:
                result={'candidates':[{'option_key':'inspect','preview_text':'检查灯光。','state_delta':{'choice':'inspect'}},
                    {'option_key':'wait','preview_text':'站在门廊等待。','state_delta':{'choice':'wait'}}]}
            elif 'You are a senior' in role:
                result={'medium':'anime_cel','art_direction':'Clean two dimensional animation with restrained color and consistent visual identity.',
                    'linework':'clean line art with tapered ends','shading':'flat two-level cel shading with subtle rim light',
                    'texture':'subtle film grain, no paper texture','base_palette':['navy blue','warm ivory','slate gray'],
                    'contrast_policy':'medium','lighting_policy':'soft_diffused','palette_policy':'low_sat_unity',
                    'forbidden_styles':['photorealistic rendering','3D game render','inconsistent art style drift','neon lighting'],
                    'rationale':'A deterministic fixture style for native integration testing.'}
            elif '背景图剧情角色泄漏验收器' in role:
                result={'forbidden_entity_matches':[],'generic_environment_entities':[],
                    'any_human_present':False,'any_character_like_subject_present':False,'reasons':['fixture response']}
            elif '关键帧语义验收器' in role:
                result={'passes':True,'missing_elements':[],'mismatch_reason':'','confidence':1.0}
            elif 'apparent age' in role:
                result={'observed_age_group':'young_adult','estimated_age_range':'20-25','confidence':1.0,'evidence':['fixture'],'violations':[]}
            elif isinstance(user,str) and 'FIELDS:\n' in user:
                fields=json.loads(user.split('FIELDS:\n',1)[1].split('\n\nOutput schema:',1)[0])
                if 'final_prompt' in user:
                    result={'final_prompt':fields.get('subject_opening','A young adult man')+
                        ', exactly one character, full body, head-to-toe with both feet fully visible, transparent background, navy raincoat, short dark hair, clean two dimensional cel shading.'}
                else:
                    result={'subject':'An empty porch lit by one warm lamp in a steady rain with clear walls and pavement.',
                        'details':['A rain-soaked courtyard beyond the doorway.'],'lighting':'Warm indoor lamp and cool ambient rain.',
                        'composition':'Wide view, all scenery visible.','style':'Clean cel-shaded anime.','negative_minimal':['text','watermark']}
            else:
                self.send_error(400,'unhandled local fixture role');return
            text=json.dumps(result,ensure_ascii=False)
            response={'id':'fixture-'+str(len(self.requests)),'model':body['model'],'object':'chat.completion','created':0,
                'choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}}
            if body.get('stream'):
                raw=('data: '+json.dumps({'id':response['id'],'model':body['model'],'choices':[{'index':0,'delta':{'content':text},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n').encode()
                return self.reply(raw,'text/event-stream')
        self.reply(json.dumps(response,ensure_ascii=False).encode(),'application/json')
    def do_GET(self):
        image=self.images.get(self.path.split('?',1)[0])
        if image is None: self.send_error(404);return
        self.reply(image,'image/png')
    def reply(self,raw,mime):
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)
