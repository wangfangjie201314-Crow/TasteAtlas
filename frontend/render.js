(function(){"use strict";
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderPlan(data, shouldScroll) {
    var result=document.getElementById("result");
    var warning=document.getElementById("warnings");
    if(warning)warning.textContent=(data.partial ? "Partial plan. " : "")+(data.warnings||[]).join(" ");
    document.getElementById("res-title").textContent =
      data.mode === "qloo_agent_tool" ? "Your taste day in " + data.city : "Your day in " + data.city + " — " + data.occasion.replace(/_/g, " ");
    document.getElementById("res-profile").textContent = data.taste_profile;

    var tl = document.getElementById("timeline");
    tl.innerHTML = "";
    data.stops.forEach(function (s) {
      var div = document.createElement("div");
      div.className = "stop";
      var tags = (s.tags || []).slice(0, 5).map(function (t) {
        return "<span>" + esc(t) + "</span>";
      }).join("");
      var imageUrl = safeImage(s.image_url);
      var img = imageUrl
        ? '<img class="thumb" src="' + esc(imageUrl) + '" alt="Preview image" tabindex="0" role="button" loading="lazy" />'
        : "";
      div.innerHTML =
        '<div class="stop-time">' + esc(s.kind === "urn:entity:place" ? (s.time || "--:--") : "Inspiration") + "</div>" +
        '<div class="stop-body">' +
          "<h3>" + esc(s.name) + "</h3>" +
          '<span class="kind">' + esc((s.kind || "").replace("urn:entity:", "")) + "</span>" +
          img +
          '<p class="why"><b>' + (data.mode === 'qloo_agent_tool' ? 'Qloo taste match:' : 'AI taste suggestion:') + '</b> ' + esc(s.why) + "</p>" +
          (s.tip ? '<p class="tip"><b>Tip:</b> ' + esc(s.tip) + "</p>" : "") +
          (tags ? '<div class="tagrow">' + tags + "</div>" : "") +
        "</div>";
      tl.appendChild(div);
    });

    document.getElementById("dropped-count").textContent =
      (data.dropped_by_critic || []).length;
    var calls = document.getElementById("qloo-calls");
    calls.innerHTML = "";
    (data.qloo_calls || []).forEach(function (c) {
      var li = document.createElement("li");
      li.textContent = c;
      calls.appendChild(li);
    });

    result.classList.remove("hidden");
    if(shouldScroll !== false)result.scrollIntoView({ behavior: "smooth" });
  }

  function safeImage(value){try{var url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password?url.href:'';}catch(_){return '';}}
  var viewer=document.createElement('dialog');viewer.id='image-viewer';viewer.setAttribute('aria-label','Image preview');
  var close=document.createElement('button');close.textContent='Close';close.type='button';
  var large=document.createElement('img');large.alt='Expanded recommendation image';large.draggable=false;
  viewer.append(close,large);document.body.appendChild(viewer);
  var controls=document.createElement('div');controls.className='image-zoom';
  ['Zoom out','Zoom in'].forEach(function(label,index){
    var button=document.createElement('button');button.type='button';button.textContent=index?'+':'−';button.setAttribute('aria-label',label);
    button.addEventListener('click',function(){zoom=Math.min(4,Math.max(1,zoom*(index?1.25:0.8)));if(zoom===1)panX=panY=0;transform();});
    controls.appendChild(button);
  });viewer.appendChild(controls);
  var zoom=1,panX=0,panY=0,drag=null,originalY=0,overflow='';
  function transform(){large.style.transform='translate('+panX+'px,'+panY+'px) scale('+zoom+')';}
  function showImage(target){if(!target.matches('img.thumb'))return;var url=safeImage(target.src);if(!url)return;zoom=1;panX=panY=0;transform();large.src=url;originalY=scrollY;overflow=document.body.style.overflow;document.body.style.overflow='hidden';viewer.showModal();}
  document.getElementById('timeline').addEventListener('click',function(e){showImage(e.target);});
  document.getElementById('timeline').addEventListener('keydown',function(e){if(e.target.matches('img.thumb')&&(e.key==='Enter'||e.key===' ')){e.preventDefault();showImage(e.target);}});
  close.addEventListener('click',function(){viewer.close();});
  viewer.addEventListener('click',function(e){if(e.target===viewer)viewer.close();});
  viewer.addEventListener('close',function(){document.body.style.overflow=overflow;window.scrollTo(0,originalY);large.removeAttribute('src');drag=null;});
  large.addEventListener('wheel',function(e){e.preventDefault();zoom=Math.min(4,Math.max(1,zoom*(e.deltaY<0?1.15:1/1.15)));transform();},{passive:false});
  large.addEventListener('pointerdown',function(e){drag={x:e.clientX,y:e.clientY,px:panX,py:panY};large.setPointerCapture(e.pointerId);});
  large.addEventListener('pointermove',function(e){if(drag){panX=drag.px+e.clientX-drag.x;panY=drag.py+e.clientY-drag.y;transform();}});
  large.addEventListener('pointerup',function(){drag=null;});
  large.addEventListener('pointercancel',function(){drag=null;});
  large.addEventListener('dblclick',function(){zoom=1;panX=panY=0;transform();});

window.renderTastePlan=renderPlan;
})();
