/* BI operation forms. No window.prompt/confirm or implicit data mutations. */
(() => {
  'use strict';
  let active=null,serial=0;
  function element(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;}
  function open(options={}){
    if(active)active.close(null);
    const previous=document.activeElement;const id='tp-bi-dialog-'+(++serial);
    const shade=element('div','tp-bi-dialog-shade');
    const dialog=element('section','tp-bi-dialog');dialog.setAttribute('role','dialog');
    dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-labelledby',id+'-title');
    const head=element('header','tp-bi-dialog-head');const title=element('h2','',options.title||'配置操作');title.id=id+'-title';
    const closeButton=element('button','tp-bi-dialog-x','×');closeButton.type='button';closeButton.setAttribute('aria-label','关闭设置');head.append(title,closeButton);
    const form=element('form','tp-bi-dialog-form');form.noValidate=false;
    if(options.description){form.append(element('p','tp-bi-dialog-intro',options.description));}
    if(options.info){form.append(element('div','tp-bi-dialog-info',options.info));}
    const nodes=new Map();
    for(const field of options.fields||[]){
      const wrap=element('div','tp-bi-dialog-field');const label=element('label','',field.label||field.name);
      const key=id+'-'+field.name;label.setAttribute('for',key);wrap.append(label);
      if(field.hint)wrap.append(element('small','tp-bi-dialog-hint',field.hint));
      let input;
      if(field.type==='select'){
        input=element('select');
        for(const entry of field.options||[]){const option=document.createElement('option');
          option.value=String(typeof entry==='string'?entry:entry.value);
          option.textContent=String(typeof entry==='string'?entry:entry.label);
          input.append(option);
        }
        if(field.value!=null)input.value=String(field.value);
      }else if(field.type==='textarea'){
        input=element('textarea');input.value=String(field.value??'');input.rows=field.rows||3;
      }else{
        input=element('input');input.type=field.type==='number'?'number':'text';
        input.value=String(field.value??'');
        if(field.min!=null)input.min=String(field.min);
        if(field.max!=null)input.max=String(field.max);
        if(field.step!=null)input.step=String(field.step);
        if(field.maxlength!=null)input.maxLength=field.maxlength;
      }
      input.id=key;input.name=field.name;input.required=field.required!==false;
      input.autocomplete='off';nodes.set(field.name,input);
      const inputRow=element('div','tp-bi-dialog-input-row');inputRow.append(input);
      if(field.type==='select'&&field.useViewer!==false){
        const fromViewer=element('button','tp-bi-dialog-from-view','使用当前图表系列');fromViewer.type='button';
        fromViewer.addEventListener('click',async()=>{
          try{
            const selected=await window.TimePilotNativeBI?.getSelectedSeries?.();
            const match=(selected?.fields||[]).find(value=>Array.from(input.options).some(o=>o.value===value));
            if(match){input.value=match;input.focus();}
            else {const note=element('small','tp-bi-dialog-hint','当前图表未选中符合要求的字段，请从列表选择。');inputRow.after(note);}
          }catch{ /* The local list remains a complete fallback. */ }
        });inputRow.append(fromViewer);
      }
      wrap.append(inputRow);form.append(wrap);
    }
    const actions=element('footer','tp-bi-dialog-actions');
    const cancel=element('button','button','取消');cancel.type='button';
    const submit=element('button','button '+(options.danger?'tp-bi-dialog-danger':'button-primary'),options.confirmText||'确定');
    submit.type='submit';actions.append(cancel,submit);form.append(actions);
    dialog.append(head,form);shade.append(dialog);document.body.append(shade);
    return new Promise(resolve=>{
      let closed=false;
      function finish(value){if(closed)return;closed=true;shade.remove();active=null;previous?.focus?.();resolve(value);}
      active={close:finish};closeButton.addEventListener('click',()=>finish(null));cancel.addEventListener('click',()=>finish(null));
      shade.addEventListener('click',event=>{if(event.target===shade&&options.dismissOnBackdrop!==false)finish(null)});
      shade.addEventListener('keydown',event=>{
        if(event.key==='Escape'){event.preventDefault();finish(null)}
        if(event.key==='Tab'){
          const focus=Array.from(dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled)'));
          if(!focus.length)return;
          const first=focus[0],last=focus[focus.length-1];
          if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
          else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
        }
      });
      form.addEventListener('submit',event=>{
        event.preventDefault();if(!form.reportValidity())return;
        const values={};for(const [name,input] of nodes){values[name]=input.type==='number'?Number(input.value):input.value;}
        finish(values);
      });
      requestAnimationFrame(()=>{(form.querySelector('input,select,textarea')||submit).focus()});
    });
  }
  function confirm(title,description,{danger=false,confirmText='确认'}={}){
    return open({title,description,danger,confirmText,fields:[],dismissOnBackdrop:false}).then(Boolean);
  }
  function text(title,defaultValue='',description=''){
    return open({title,description,fields:[{name:'value',label:'名称或说明',value:defaultValue,maxlength:160}]}).then(v=>v?.value?.trim()||null);
  }
  window.TimePilotBIDialogs={open,confirm,text,close:()=>active?.close(null)};
})();
