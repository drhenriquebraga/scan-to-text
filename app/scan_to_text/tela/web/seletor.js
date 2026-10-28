/* Seletor de idioma com bandeira.

   Copiado do local-suite v8.3 (app/radai_languages.py, bloco
   `radai-idioma-js` de `_bloco_completo`: rotuloDe, montarLista, montarGrade
   e o teclado das duas formas; commit 55cffa9, Apache-2.0).

   Mudancas: a tabela de idiomas chega de GET /api/idiomas, com a bandeira no
   quinto campo, como no JSON do original; a escolha vai para quem montou
   (app.js), que grava no config.json e troca os textos sem recarregar; o
   tradutor por dicionario de frases do original nao veio, porque aqui os
   textos moram em i18n/<idioma>.json. Um so ouvinte de clique fora fecha
   qualquer lista aberta, porque a tela remonta o seletor a cada troca. */
(function () {
  'use strict';
  var _nSel = 0;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // A bandeira e o nome, na lingua dele. `lang` e `dir` por linha: o leitor
  // de tela pronuncia o nome na propria lingua, e o arabe se escreve da
  // direita para a esquerda mesmo numa tela em portugues.
  function rotuloDe(x) {
    return '<span class="rl-band" aria-hidden="true">' + (x[4] || '') + '</span>'
      + '<span class="rl-nome" lang="' + esc(x[0]) + '" dir="' + (x[3] ? 'rtl' : 'ltr') + '">'
      + esc(x[2]) + '</span>';
  }

  function montarLista(alvo, cur, idiomas, escolher) {
    var cx = document.createElement('div'); cx.className = 'radai-lang';
    var id = 'radai-lang-lista-' + (++_nSel);
    cx.innerHTML = '<button type="button" class="rl-bt" aria-haspopup="listbox" '
      + 'aria-expanded="false" aria-controls="' + id + '">' + rotuloDe(cur)
      + '<span class="rl-seta" aria-hidden="true"></span></button>'
      + '<div class="lista" id="' + id + '" role="listbox">' + idiomas.map(function (x) {
        var on = (x[0] === cur[0]);
        return '<button type="button" role="option" class="rl-op' + (on ? ' on' : '')
          + '" data-cod="' + esc(x[0]) + '" aria-selected="' + (on ? 'true' : 'false') + '">'
          + rotuloDe(x) + '<span class="rl-cod">' + esc(x[1]) + '</span></button>';
      }).join('')
      + '</div>';
    alvo.appendChild(cx);
    var bt = cx.querySelector('.rl-bt'), lista = cx.querySelector('.lista');
    var ops = Array.prototype.slice.call(lista.querySelectorAll('.rl-op'));
    function abrir(focar) {
      cx.classList.add('on'); bt.setAttribute('aria-expanded', 'true');
      var f = lista.querySelector('.rl-op.on') || ops[0];
      // o foco vai DEPOIS de a lista ficar visivel: `visibility:hidden`
      // recusa o foco, e a transicao troca a visibilidade no mesmo quadro
      if (focar && f) setTimeout(function () { try { f.focus(); } catch (e) {} }, 0);
    }
    function fechar(devolver) {
      cx.classList.remove('on'); bt.setAttribute('aria-expanded', 'false');
      if (devolver) { try { bt.focus(); } catch (e) {} }
    }
    cx.radaiFechar = fechar;
    bt.addEventListener('click', function (e) {
      e.preventDefault(); if (cx.classList.contains('on')) fechar(false); else abrir(true);
    });
    bt.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); abrir(true); }
    });
    // O teclado da lista: setas, Home/End, Esc devolve o foco ao botao, Tab
    // fecha. Enter e espaco sao do proprio `<button>` de cada linha.
    lista.addEventListener('keydown', function (e) {
      var i = ops.indexOf(document.activeElement), n = ops.length, k = -1;
      if (e.key === 'Escape') { e.preventDefault(); fechar(true); return; }
      if (e.key === 'Tab') { fechar(false); return; }
      if (e.key === 'ArrowDown') k = (i + 1) % n;
      else if (e.key === 'ArrowUp') k = (i - 1 + n) % n;
      else if (e.key === 'Home') k = 0;
      else if (e.key === 'End') k = n - 1;
      if (k >= 0) { e.preventDefault(); ops[k].focus(); }
    });
    ops.forEach(function (o) {
      o.addEventListener('click', function () {
        var c = o.getAttribute('data-cod');
        ops.forEach(function (p) {
          var s = (p === o);
          p.classList.toggle('on', s); p.setAttribute('aria-selected', s ? 'true' : 'false');
        });
        fechar(true);
        escolher(c, 'lista');
      });
    });
  }

  function montarGrade(alvo, cur, idiomas, escolher) {
    var cx = document.createElement('div'); cx.className = 'radai-lang rl-grade';
    cx.setAttribute('role', 'radiogroup');
    var rotulo = alvo.getAttribute('aria-labelledby');
    if (rotulo) cx.setAttribute('aria-labelledby', rotulo);
    cx.innerHTML = idiomas.map(function (x) {
      var on = (x[0] === cur[0]);
      return '<button type="button" role="radio" class="rl-op' + (on ? ' on' : '')
        + '" data-cod="' + esc(x[0]) + '" aria-checked="' + (on ? 'true' : 'false') + '" tabindex="'
        + (on ? '0' : '-1') + '">' + rotuloDe(x) + '</button>';
    }).join('');
    alvo.appendChild(cx);
    var ops = Array.prototype.slice.call(cx.querySelectorAll('.rl-op'));
    ops.forEach(function (o) {
      o.addEventListener('click', function () {
        ops.forEach(function (p) {
          var s = (p === o);
          p.classList.toggle('on', s); p.setAttribute('aria-checked', s ? 'true' : 'false');
          p.tabIndex = s ? 0 : -1;
        });
        escolher(o.getAttribute('data-cod'), 'grade');
      });
    });
    // RADIOGROUP: as setas andam entre as doze, sem escolher.
    cx.addEventListener('keydown', function (e) {
      var i = ops.indexOf(document.activeElement), n = ops.length, k = -1;
      if (i < 0) return;
      var rtl = getComputedStyle(cx).direction === 'rtl';
      var frente = rtl ? 'ArrowLeft' : 'ArrowRight', tras = rtl ? 'ArrowRight' : 'ArrowLeft';
      if (e.key === frente || e.key === 'ArrowDown') k = (i + 1) % n;
      else if (e.key === tras || e.key === 'ArrowUp') k = (i - 1 + n) % n;
      else if (e.key === 'Home') k = 0;
      else if (e.key === 'End') k = n - 1;
      if (k >= 0) { e.preventDefault(); ops[i].tabIndex = -1; ops[k].tabIndex = 0; ops[k].focus(); }
    });
  }

  document.addEventListener('click', function (e) {
    Array.prototype.forEach.call(document.querySelectorAll('.radai-lang.on'), function (cx) {
      if (!cx.contains(e.target) && cx.radaiFechar) cx.radaiFechar(false);
    });
  });

  // `idiomas`: a lista de GET /api/idiomas. `focar` devolve o foco a forma
  // que fez a escolha, porque remontar apaga o botao que estava focado.
  window.SeletorIdioma = {
    montar: function (idiomas, atual, escolher, focar) {
      var tabela = idiomas.map(function (i) { return [i.codigo, i.sigla, i.nome, i.rtl, i.bandeira]; });
      var cur = null;
      tabela.forEach(function (x) { if (x[0] === atual) cur = x; });
      if (!cur) tabela.forEach(function (x) { if (x[0] === 'pt-BR') cur = x; });
      if (!cur) cur = tabela[0];
      if (!cur) return;
      Array.prototype.forEach.call(document.querySelectorAll('[data-radai-idiomas]'), function (alvo) {
        var velho = alvo.querySelector('.radai-lang');
        if (velho) alvo.removeChild(velho);
        if (alvo.getAttribute('data-radai-idiomas') === 'grade') montarGrade(alvo, cur, tabela, escolher);
        else montarLista(alvo, cur, tabela, escolher);
      });
      var f = null;
      if (focar === 'lista') f = document.querySelector('.topo .rl-bt');
      else if (focar === 'grade') f = document.querySelector('.rl-grade .rl-op.on');
      if (f) { try { f.focus(); } catch (e) {} }
    }
  };
})();
