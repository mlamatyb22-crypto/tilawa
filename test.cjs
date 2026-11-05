const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const elements=new Map();function el(){return {value:'',textContent:'',disabled:false,dataset:{},classList:{add(){},remove(){},toggle(){}},setAttribute(){},removeAttribute(){},addEventListener(){},showModal(){},close(){},open:false,style:{},append(){},replaceChildren(){},pause(){},play(){return Promise.resolve()},scrollIntoView(){}}}const storage={};
class Audio{constructor(){this.currentTime=0;this.duration=10}setAttribute(){}play(){return Promise.resolve()}pause(){}}
const context={console,Audio,URL,Blob,AbortSignal,setTimeout,clearTimeout,localStorage:{getItem:k=>storage[k],setItem:(k,v)=>storage[k]=v},document:{getElementById:id=>{if(!elements.has(id))elements.set(id,el());return elements.get(id)},querySelectorAll:()=>[],createElement:()=>el()},window:{addEventListener(){}},fetch:()=>Promise.reject(Error('network unavailable')),Option:class{},navigator:{},indexedDB:{open(){throw Error('unavailable')}}};vm.createContext(context);const code=fs.readFileSync(__dirname+'/index.html','utf8').match(/<script>([\s\S]*)<\/script>/)[1].replace('sync();load();','sync();');vm.runInContext(code,context);const run=s=>vm.runInContext(s,context);
(async()=>{
assert.equal(run('JSON.stringify(plan(2,4,3,"group"))'),'[2,3,4,2,3,4,2,3,4]');
assert.equal(run('JSON.stringify(plan(2,4,3,"verse"))'),'[2,2,2,3,3,3,4,4,4]');
assert.equal(run('JSON.stringify(plan(2,4,0,"group"))'),'[2,3,4]');
run('corpus=[{name:"اختبار",ayahs:Array.from({length:7},(_,i)=>({number:i+1,numberInSurah:i+1,text:"fixture"}))}];settings.from=2;settings.to=4;links=Array(7).fill("https://example.test/audio.mp3");rangeReady=true;settings.repeats="1";settings.scope="group";settings.mode="read";start()');
assert.equal(run('queue.join(",")'),'2,3,4');assert.equal(run('phase'),'playing');
await run('$("pause").onclick()');assert.equal(run('phase'),'paused');await run('$("pause").onclick()');assert.equal(run('phase'),'playing');
run('pos=2;audio.onended()');assert.equal(run('phase'),'reading');run('done()');assert.equal(run('phase'),'complete');
run('settings.repeats="0";start();pos=2;audio.onended()');assert.equal(run('phase'),'gap');run('stop()');assert.equal(run('timer'),null);
run('settings.repeats="1";settings.mode="listen";start();pos=2;audio.onended()');assert.equal(run('phase'),'complete');
run('start();audio.onerror()');assert.equal(run('phase'),'idle');assert.match(run('$("error").textContent'),/لم يكتمل/);
run('start(true);pos=2;audio.onended()');assert.equal(run('phase'),'own');run('$("own").onended()');assert.equal(run('phase'),'complete');
run('settings.from=5;settings.to=7;$("mastered").onclick()');assert.equal(run('marks["1:5-7"].state'),'mastered');assert.equal(run('settings.from'),5);assert.ok(storage['tilawa-v1']);
run('settings.from=1;settings.to=3;$("from").value="99";$("from").onchange()');assert.equal(run('settings.from'),7);assert.equal(run('settings.to'),7);
/* الخطة اليومية: سبع آيات اصطناعية، كل آية ٣ ثوانٍ، التكرار مرة، فتكلفة الآية ٦ ثوانٍ */
run('trk.cursor={surah:1,ayah:1};trk.active=null;trk.done=[];trk.log={};settings.repeats="1";settings.reciter="ar.minshawi";settings.surah=1');
run('trk.minutes=0.375');assert.equal(run('JSON.stringify(currentChunk())').includes('"to":3'),true);
run('trk.minutes=0.5');assert.equal(run('currentChunk().to'),4);
run('trk.minutes=0.625');assert.equal(run('currentChunk().to'),5);
run('trk.minutes=0.75');assert.equal(run('currentChunk().to'),7,'آية أخيرة منفردة تُضم للمقطع');
run('trk.minutes=0.375');
assert.equal(run('JSON.stringify(advance({surah:1,ayah:1},{to:3},1))'),'{"surah":1,"ayah":4}');
assert.equal(run('advance({surah:1,ayah:1},{to:7},-1)'),null);
assert.equal(run('advance({surah:1,ayah:1},{to:7},1).surah'),2);
run('$("pFinish").onclick()');assert.equal(run('trk.done.length'),1);assert.equal(run('trk.cursor.ayah'),4);assert.equal(run('trk.done[0].to'),3);
assert.ok(JSON.parse(storage['tilawa-v1']).plan.done.length===1);
assert.equal(run('simulate(1).count'),1);
/* ساعة وقت التدريب: تعدّ أثناء التشغيل فقط */
run('trk.log={};phase="playing";tick()');assert.equal(run('trk.log[today()]'),1);
run('phase="paused";tick()');assert.equal(run('trk.log[today()]'),1);
run('phase="idle"');
/* معايرة مدة الآية من الصوت الفعلي */
run('dur={};speed={};phase="playing";queue=[2];pos=0;audio.onloadedmetadata()');assert.equal(run('dur["ar.minshawi:1:2"]'),10);assert.equal(run('ayahSeconds(1,2)'),10);assert.equal(run('speed["ar.minshawi"].n'),1);
run('stop()');
await run('load()');assert.equal(run('corpus.length'),0);assert.match(run('$("error").textContent'),/تعذر تحميل القرآن/);assert.equal(run('$("start").disabled'),true);run('stop()');
console.log('PASS: group/verse/infinite queues, pause/resume, reading transition, comparison, error handling, evaluation persistence, range limits, network failure.');
})().catch(e=>{console.error(e);process.exitCode=1});
