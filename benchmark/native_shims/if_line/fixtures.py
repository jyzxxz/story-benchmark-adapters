"""Deterministic localhost fixtures. These responses are not story generation."""
from http.server import BaseHTTPRequestHandler
import json

class FixedProvider(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    requests = []
    chapter_count = 1
    model = 'fixed-test-model'
    def log_message(self,*args): pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(body)
        role=body['messages'][0]['content']
        if '小说策划师' in role:
            result={'worldview':'测试世界','characters':[],'character_relations':'','main_conflict':'等待',
                    'emotional_line':'','style_rules':'简体中文','ending_constraints':'','forbidden_points':[],'writing_notes':[]}
        elif '大纲策划师' in role:
            result={'chapters':[{'chapter_index':i,'title':f'第{i}章','summary':'等待雨停。','scene':'门廊'}
                                for i in range(1,self.chapter_count+1)]}
        elif 'Script IR' in role:
            result={'segments':[{'text':'雨继续下着。','kind':'narration','scene_key':'s1'}],
                    'scenes':[{'scene_key':'s1','title':'门廊','location':'门廊'}]}
        elif '小说作家' in role:
            result={'chapter_index':1,'title':'第一章','content':'雨继续下着。'}
        elif '互动叙事分支规划器' in role:
            result={'candidates':[
                {'option_key':'enter','preview_text':'林澈推开实验楼的门。','state_delta':{'location':'实验楼'}},
                {'option_key':'protect','preview_text':'林澈先扶稳周遥，再检查收音机。','state_delta':{'priority':'protect'}}]}
        elif 'You are a senior' in role:
            result={'medium':'anime_cel','art_direction':'Clean two dimensional animation with restrained color and consistent visual identity.',
                'linework':'clean line art with tapered ends','shading':'flat two-level cel shading with subtle rim light',
                'texture':'subtle film grain, no paper texture','base_palette':['navy blue','warm ivory','slate gray'],
                'contrast_policy':'medium','lighting_policy':'soft_diffused','palette_policy':'low_sat_unity',
                'forbidden_styles':['photorealistic rendering','3D game render','inconsistent art style drift','neon lighting'],
                'rationale':'A simple fixture style for engineering integration testing.'}
        else:
            self.send_error(400)
            return
        text=json.dumps(result,ensure_ascii=False)
        if body.get('stream'):
            chunks=[{'id':'fixed-stream','model':self.model,'choices':[{'index':0,
                'delta':{'content':text[:20]},'finish_reason':None}]},
                {'id':'fixed-stream','model':self.model,'choices':[{'index':0,
                'delta':{'content':text[20:]},'finish_reason':'stop'}]}]
            raw=(''.join('data: '+json.dumps(c,ensure_ascii=False)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
            mime='text/event-stream'
        else:
            raw=json.dumps({'id':'fixed-'+str(len(self.requests)),'model':self.model,'object':'chat.completion',
                'created':0,'choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}},ensure_ascii=False).encode()
            mime='application/json'
        self.send_response(200)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
