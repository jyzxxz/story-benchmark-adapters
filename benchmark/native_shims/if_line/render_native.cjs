/* Actual frozen Vue player, compiled only into an external temporary harness. */
const fs=require('fs'), path=require('path'), http=require('http');
const [repo,deps,inputFile,outDir]=process.argv.slice(2);
const esbuild=require(path.join(deps,'esbuild'));
const sfc=require(path.join(deps,'@vue/compiler-sfc'));
const {chromium}=require(path.join(deps,'playwright'));
const source=path.join(repo,'frontend/src');
const input=JSON.parse(fs.readFileSync(inputFile,'utf8'));
fs.mkdirSync(outDir,{recursive:true});
const styles=[];
const plugin={name:'native-vue-readonly',setup(build){
  build.onResolve({filter:/^@\//},args=>({path:path.join(source,args.path.slice(2))+'.ts'}));
  build.onLoad({filter:/\.vue$/},async args=>{
    const raw=fs.readFileSync(args.path,'utf8');
    const {descriptor}=sfc.parse(raw,{filename:args.path});
    const id='native-'+require('crypto').createHash('sha256').update(args.path).digest('hex').slice(0,12);
    const script=sfc.compileScript(descriptor,{id,inlineTemplate:true,genDefaultAs:'__component'});
    for(const style of descriptor.styles){
      const result=sfc.compileStyle({source:style.content,filename:args.path,id:'data-v-'+id,scoped:style.scoped});
      if(result.errors.length) throw result.errors[0];
      styles.push(result.code);
    }
    return {contents:script.content+`\n__component.__scopeId='data-v-${id}'; export default __component;`,
      loader:'ts',resolveDir:path.dirname(args.path)};
  });
}};
(async()=>{
  const built=await esbuild.build({stdin:{contents:`
    import {createApp,h} from 'vue';
    import Player from ${JSON.stringify(path.join(source,'components/VNGraphPlayer.vue'))};
    import {buildPlaybackPlan} from ${JSON.stringify(path.join(source,'utils/vnGraphPlayer.ts'))};
    window.load=(graph,base)=>{window.nativePlan=buildPlaybackPlan(graph,{resBaseUrl:base}).beats;
      window.nativeBeatSequence=0;
      createApp({render:()=>h(Player,{graph,resBaseUrl:base,onBeatChange:b=>{window.nativeBeat=JSON.parse(JSON.stringify(b));window.nativeBeatSequence++;}})}).mount('#app');};
    `,resolveDir:outDir,loader:'ts'},bundle:true,write:false,platform:'browser',format:'iife',
    nodePaths:[deps],plugins:[plugin],define:{__VUE_OPTIONS_API__:'true',__VUE_PROD_DEVTOOLS__:'false'}});
  const script=built.outputFiles[0].text;
  const html=`<!doctype html><html><head><meta charset="UTF-8"><style>body{margin:0}#app{width:1280px}${styles.join('\n')}</style></head><body><div id="app"></div><script>${script}</script></body></html>`;
  const server=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  let browser;
  try{
    browser=await chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900},deviceScaleFactor:1});
    const api=new URL(input.api_base_url);
    if(process.env.IFLINE_RENDER_SID) await context.addCookies([{name:'sid',value:process.env.IFLINE_RENDER_SID,domain:api.hostname,path:'/'}]);
    const page=await context.newPage();
    // Relative media URLs are served by the native API, not the harness.
    await page.route('**/media/**',route=>route.continue({url:input.api_base_url+new URL(route.request().url()).pathname}));
    await page.goto('http://127.0.0.1:'+server.address().port);
    await page.evaluate(x=>window.load(x.graph,x.api_base_url),input);
    await page.waitForFunction(()=>window.nativeBeat);
    const result={renderer:'frozen_native_VNGraphPlayer.vue',capture_method:'offscreen_native',
      observer:'headless_chromium',clock_id:'renderer-process-'+process.pid,beats:[],choices:[],plan:await page.evaluate(()=>window.nativePlan)};
    const save=()=>fs.writeFileSync(path.join(outDir,'playback.json'),JSON.stringify(result,null,2));
    let choiceNo=input.choice_offset||0;
    let visibleChars=0;
    while(true){
      const beat=await page.evaluate(()=>window.nativeBeat);
      const beatSequence=await page.evaluate(()=>window.nativeBeatSequence);
      const availableNs=process.hrtime.bigint().toString();
      await page.evaluate(async()=>{await Promise.all([...document.images].map(i=>i.decode().catch(()=>null)));
        const urls=[window.nativeBeat.stage.backgroundUrl,window.nativeBeat.stage.illustrationUrl].filter(Boolean);
        window.failedCssImages=[];
        await Promise.all(urls.map(url=>new Promise(r=>{const i=new Image();i.onload=r;i.onerror=()=>{window.failedCssImages.push(url);r();};i.src=url;})));});
      const index=result.beats.length;
      const ui=path.join(outDir,index+'-ui.png'),clean=path.join(outDir,index+'-clean.png');
      const player=page.locator('.vn-player');
      await player.screenshot({path:ui,animations:'disabled'});
      await page.addStyleTag({content:'.vn-player-dialogue,.vn-player-controls,.vn-player-debug{visibility:hidden!important}'});
      await player.screenshot({path:clean,animations:'disabled'});
      await page.evaluate(()=>document.head.lastElementChild.remove());
      const missing=await page.evaluate(()=>[...window.failedCssImages,...[...document.images].filter(i=>!i.complete||i.naturalWidth===0).map(i=>i.src)]);
      result.beats.push({...beat,clean_path:clean,ui_path:ui,missing_images:missing,
        observed_utc:new Date().toISOString(),monotonic_ns:process.hrtime.bigint().toString()});
      save();
      // Choice beats contain the native UI marker “（请选择）”, not story prose.
      if(!beat.options.length) visibleChars += [...(beat.text||'')].length;
      if(visibleChars>=input.remaining_chars) break;
      if(beat.options.length){
        const indices=input.choice_indices;
        const selected=indices[Math.min(choiceNo,indices.length-1)];
        if(!Number.isInteger(selected)||selected<0||selected>=beat.options.length) throw Error('choice_index_out_of_range');
        if(input.reading_delay_seconds) await new Promise(r=>setTimeout(r,input.reading_delay_seconds*1000));
        const clickedNs=process.hrtime.bigint().toString();
        await page.locator('.vn-player-option').nth(selected).click();
        await page.waitForFunction(previous=>window.nativeBeatSequence>previous,beatSequence);
        result.choices.push({beat_index:index,native_node_index:beat.nodeIndex,selected_index:selected,options:beat.options,
          available_monotonic_ns:availableNs,clicked_monotonic_ns:clickedNs,
          committed_monotonic_ns:process.hrtime.bigint().toString(),committed_utc:new Date().toISOString(),
          clock_id:result.clock_id,policy_choice_ordinal:choiceNo});choiceNo++;save();
      } else {
        const next=page.locator('.vn-player-btn--primary');
        if(await next.isDisabled()) break;
        await next.click();
      }
      await page.waitForFunction(previous=>window.nativeBeatSequence>previous,beatSequence);
    }
    fs.writeFileSync(path.join(outDir,'playback.json'),JSON.stringify(result,null,2));
  }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{process.stderr.write(String(e.stack||e)+'\n');process.exitCode=1;});
