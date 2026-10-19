(function () {
  'use strict';
  var form=document.getElementById('resolve-form'), choices=document.getElementById('choose');
  var result=document.getElementById('result'), err=document.getElementById('error');
  var busy=document.getElementById('busy'), profile=null;
  function store(k,v){try{sessionStorage.setItem(k,v);}catch(_){}}
  function read(k){try{return sessionStorage.getItem(k);}catch(_){return null;}}
  function nav(stage){if(location.hash!=='#'+stage)history.replaceState(null,'','#'+stage);}
  function showError(message){err.textContent=message;err.classList.remove('hidden');}
  function loading(message){
    busy.textContent=message||'';busy.classList.toggle('hidden',!message);
    ['city','favorite','resolve-button','plan-button'].forEach(function(id){document.getElementById(id).disabled=!!message;});
  }
  function usage(value){if(value)document.getElementById('usage').textContent='Local Qloo request budget: '+value.used+' / '+value.limit+' · 0 LLM calls';}
  async function api(action,data){
    var response=await fetch('/api/tools/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    var body=await response.json();
    if(!response.ok){var failure=Error(typeof body.detail==='string'?body.detail:'Please check the input fields.');failure.status=response.status;throw failure;}
    usage(body.usage);return body;
  }
  function present(state,restoring){
    profile=state;store('tasteatlas-profile',state.profile_ref);usage(state.usage);
    if(state.state==='complete'){
      choices.classList.add('hidden');window.renderTastePlan(state.result,!restoring);nav('result');
      document.getElementById('health').textContent=(restoring?'Cached live Qloo result':'Live Qloo result')+' · guided tool · 0 LLM calls';return;
    }
    result.classList.add('hidden');
    if(state.state!=='ready'){
      choices.classList.add('hidden');
      if(state.state==='planning'){nav('choose');return;}
      showError('This plan failed. Your draft is kept. Resolve again to start a new plan; no automatic Qloo retry was sent.');return;
    }
    var field=document.getElementById('entity-matches');field.replaceChildren();
    var legend=document.createElement('legend');legend.textContent='Qloo matches';legend.className='fine';field.appendChild(legend);
    state.matches.forEach(function(match,index){
      var label=document.createElement('label');label.className='match-option';
      var radio=document.createElement('input');radio.type='radio';radio.name='entity';radio.value=match.entity_id;radio.required=true;
      var text=document.createElement('span');
      var name=document.createElement('strong');name.textContent=match.name;
      var kind=document.createElement('small');kind.textContent=match.kind.replace('urn:entity:','');
      var description=document.createElement('small');description.textContent=match.description;
      text.append(name,kind,description);label.append(radio,text);field.appendChild(label);
    });
    choices.classList.remove('hidden');nav('choose');
  }
  ['city','favorite'].forEach(function(id){
    var input=document.getElementById(id);input.value=read('tasteatlas-'+id)||'';
    input.addEventListener('input',function(){
      store('tasteatlas-'+id,input.value);store('tasteatlas-profile','');profile=null;
      choices.classList.add('hidden');result.classList.add('hidden');nav('plan');
    });
  });
  form.addEventListener('submit',async function(event){
    event.preventDefault();err.classList.add('hidden');choices.classList.add('hidden');result.classList.add('hidden');
    loading('Resolving your favorite with Qloo…');
    try{present(await api('resolve',{city:document.getElementById('city').value.trim(),favorite:document.getElementById('favorite').value.trim()}));}
    catch(error){showError(error.message);}finally{loading('');}
  });
  document.getElementById('choose-form').addEventListener('submit',async function(event){
    event.preventDefault();if(!profile)return;
    var selected=document.querySelector('input[name="entity"]:checked');if(!selected)return;
    err.classList.add('hidden');loading('Querying Qloo for city places and cultural inspiration…');
    try{
      var plan=await api('plan',{profile_ref:profile.profile_ref,entity_id:selected.value});
      profile.state='complete';profile.result=plan;profile.usage=plan.usage;present(profile);
      document.getElementById('health').textContent='Live Qloo result · guided tool · 0 LLM calls';
    }catch(error){showError(error.message);}finally{loading('');}
  });
  fetch('/api/health').then(function(r){return r.json();}).then(function(h){
    if(profile&&profile.state==='complete')return;
    document.getElementById('health').textContent=h.qloo_configured&&h.guided_enabled?
      'Qloo credential available · resolve to verify a live result':'Setup required · no live integration assumed';
  }).catch(function(){document.getElementById('health').textContent='Backend unavailable';});
  var ref=read('tasteatlas-profile');
  window.addEventListener('beforeunload',function(){store('tasteatlas-scroll',String(scrollY));});
  async function restore(ref){
    var state=await api('state',{profile_ref:ref});present(state,true);
    while(state.state==='planning'){
      loading('Your Qloo request is still running. Restoring its result…');
      await new Promise(function(done){setTimeout(done,1500);});
      state=await api('state',{profile_ref:ref});present(state,true);
    }
    window.scrollTo(0,Number(read('tasteatlas-scroll')||0));
  }
  if(ref){loading('Restoring your private cached plan…');restore(ref)
    .catch(function(error){if(error.status===410){store('tasteatlas-profile','');nav('plan');}showError(error.message+' Reload to check again after the server recovers.');}).finally(function(){loading('');});}
  else nav('plan');
})();
