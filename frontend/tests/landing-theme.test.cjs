const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname,'../js/landing-theme.js'),'utf8');
function boot({saved, dark=false, blocked=false}={}) {
 const events={}, attrs={}, label={}, media={matches:dark,addEventListener:(n,f)=>events.media=f};
 const button={setAttribute:(k,v)=>attrs[k]=v,querySelector:()=>label,addEventListener:(n,f)=>events.click=f};
 const root={dataset:{}};
 const storage={getItem(){if(blocked)throw Error('blocked');return saved;},setItem(k,v){if(blocked)throw Error('blocked');saved=v;}};
 vm.runInNewContext(source,{window:{matchMedia:()=>media},localStorage:storage,document:{documentElement:root,getElementById:()=>button,addEventListener:(n,f)=>events.ready=f}});
 events.ready();return {root,attrs,label,click:()=>events.click(),system:d=>events.media({matches:d}),saved:()=>saved};
}
test('system preference initializes and follows changes until explicit selection',()=>{
 const app=boot({dark:true});assert.equal(app.root.dataset.theme,'dark');app.system(false);assert.equal(app.root.dataset.theme,'light');app.click();app.system(false);assert.equal(app.root.dataset.theme,'dark');assert.equal(app.attrs['aria-pressed'],'true');assert.equal(app.label.textContent,'Light mode');
});
test('toggle persists across reload and overrides system preference',()=>{
 const app=boot();app.click();assert.equal(app.saved(),'dark');const next=boot({saved:app.saved()});assert.equal(next.root.dataset.theme,'dark');next.click();assert.equal(next.saved(),'light');assert.equal(next.attrs['aria-label'],'Switch to dark mode');
});
test('blocked storage and invalid saved values still allow switching',()=>{
 const app=boot({blocked:true});app.click();assert.equal(app.root.dataset.theme,'dark');assert.equal(boot({saved:'invalid',dark:true}).root.dataset.theme,'dark');
});
