/* Matrix dot loader do Transitions.dev: o montador dos pontos.

   Receita do repositorio publico github.com/Jakubantalik/transitions.dev
   (transitions/matrix-dot-loader): o JS cria dezesseis <i> por matriz e da a
   cada um um atraso --d (ms) dentro do ciclo de cor compartilhado. A variante
   e so a tabela de atrasos; aqui vale a `scan`, coluna * ciclo / 10, que
   varre as colunas na direcao da leitura (a grade segue o `dir` da pagina).
   Credito em Configuracoes > Referencias. */
(function () {
  'use strict';

  function ciclo(el) {
    var v = parseFloat(getComputedStyle(el).getPropertyValue('--matrix-cycle'));
    return isFinite(v) && v > 0 ? v : 1200;
  }

  function montar(el) {
    if (!el || el.childElementCount) return;
    var c = ciclo(el);
    for (var i = 0; i < 16; i++) {
      var ponto = document.createElement('i');
      ponto.style.setProperty('--d', String((i % 4) * c / 10));
      el.appendChild(ponto);
    }
  }

  window.MatrizDePontos = { montar: montar };
})();
