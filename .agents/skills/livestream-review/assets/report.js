/* 直播复盘可视化页交互脚本（阶段折叠 + 时间线类型过滤）
   由 livestream-review skill 维护；运行时以相对路径 report.js 与页面同目录放置。 */
(function(){
  var phases=document.querySelectorAll('.phase');
  phases.forEach(function(p){
    p.querySelector('.ph-head').addEventListener('click',function(){p.classList.toggle('closed');});
  });
  var btns=document.querySelectorAll('.tl-bar button');
  btns.forEach(function(b){
    b.addEventListener('click',function(){
      btns.forEach(function(x){x.classList.remove('on');});
      b.classList.add('on');
      var f=b.getAttribute('data-f');
      document.querySelectorAll('.ph-body .ev').forEach(function(ev){
        ev.classList.toggle('hidden', f!=='all' && !ev.classList.contains(f));
      });
      phases.forEach(function(p){p.classList.remove('closed');});
    });
  });
})();
