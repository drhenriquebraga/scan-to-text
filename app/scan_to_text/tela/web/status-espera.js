/* A espera da tela inicial e a linha de conexao com o motor local.

   Matrix dot loader e shimmer text do Transitions.dev, com o montador de
   efeitos.js; credito em Configuracoes > Referencias e no NOTICE.

   O modulo nao faz requisicao: app.js avisa o comeco, a resposta e a falha de
   cada consulta de estado que ja faz, e a linha so apresenta isso. O ponto
   fala da ligacao HTTP com o motor local, e nao das pastas, da rede ou da
   leitura: um destino fora do ar convive com o ponto verde, e quem fala dele
   sao os avisos. */
(function () {
  'use strict';

  // Escolhas para a consulta de 1 s com a pagina visivel e 5 s oculta: mudou
  // o intervalo em app.js, reveja estes.
  var LENTA_MS = 800, INSTAVEL_MS = 6000, INATIVA_MS = 15000, FALHAS_ATE_INATIVA = 3;
  var ESTADOS = { parado: true, ocioso: true, trabalhando: true, parando: true };

  var situacao = 'verificando', falhas = 0, ultimaValida = 0, desde = 0, tempo = null;
  var montado = false, texto = null, linha = null, matriz = null, indicadores = [];

  function $(id) { return document.getElementById(id); }

  function traduzir(chave, vars) {
    var t = window.STTTraduzirStatus;
    return typeof t === 'function' ? t(chave, vars) : '';
  }

  // A copia dentro do aviso do motor fica calada: o aviso ja e anunciado, e
  // quem anuncia a troca de situacao e a linha da tela inicial. O tempo muda
  // a cada consulta e nunca e anunciado.
  function criarIndicador(copia) {
    var raiz = document.createElement('div'), ponto = document.createElement('span');
    var rotulo = document.createElement('span'), medida = document.createElement('span');
    raiz.className = 'stt-conexao';
    ponto.className = 'stt-conexao-ponto';
    ponto.setAttribute('aria-hidden', 'true');
    rotulo.className = 'stt-conexao-rotulo';
    if (copia) rotulo.setAttribute('aria-live', 'off');
    else {
      rotulo.setAttribute('role', 'status');
      rotulo.setAttribute('aria-live', 'polite');
      rotulo.setAttribute('aria-atomic', 'true');
    }
    medida.className = 'stt-conexao-metrica';
    medida.setAttribute('aria-hidden', 'true');
    raiz.appendChild(ponto);
    raiz.appendChild(rotulo);
    raiz.appendChild(medida);
    indicadores.push({ raiz: raiz, rotulo: rotulo, medida: medida });
    return raiz;
  }

  function pintar() {
    if (!montado) return;
    var rotulo = traduzir(situacao);
    var medida = tempo !== null && situacao !== 'inativa' ? traduzir('tempo', { ms: tempo }) : '';
    var dica = ultimaValida
      ? traduzir('ultima_resposta', { hora: new Date(ultimaValida).toLocaleTimeString(document.documentElement.lang || undefined) })
      : traduzir('aguardando_resposta');
    indicadores.forEach(function (it) {
      it.raiz.setAttribute('data-conexao', situacao);
      if (it.rotulo.textContent !== rotulo) it.rotulo.textContent = rotulo;
      if (it.medida.textContent !== medida) it.medida.textContent = medida;
      if (it.raiz.title !== dica) it.raiz.title = dica;
    });
    // Espera de verdade: rodando sem nada a ler, sem aviso e com o motor
    // respondendo. Com aviso, volta o ponto de alerta de app.js.
    var ocioso = $('start').getAttribute('data-estado') === 'ocioso'
      && !$('avisos').children.length && $('motor-parou').hidden;
    var esperando = ocioso && (situacao === 'ativa' || situacao === 'lenta');
    linha.setAttribute('data-stt-ocioso', String(ocioso));
    matriz.hidden = !ocioso;
    matriz.setAttribute('data-ativo', String(esperando));
    texto.classList.toggle('stt-espera-texto', esperando);
  }

  function definir(nova) {
    situacao = nova;
    pintar();
  }

  function valido(e) {
    return !!e && ESTADOS[e.estado] === true && typeof e.configurado === 'boolean';
  }

  function iniciarConsulta() {
    if (!desde) desde = Date.now();
  }

  // So uma resposta valida limpa as falhas: GET que devolve HTML ou JSON de
  // outra coisa conta como falha.
  function resposta(e, ms) {
    if (!valido(e)) {
      falha(0);
      return false;
    }
    falhas = 0;
    ultimaValida = desde = Date.now();
    tempo = Number.isFinite(ms) ? Math.max(0, Math.round(ms)) : null;
    definir(tempo !== null && tempo > LENTA_MS ? 'lenta' : 'ativa');
    return true;
  }

  function falha(status) {
    if (!desde) desde = Date.now();
    falhas++;
    tempo = null;
    definir(status === 403 || falhas >= FALHAS_ATE_INATIVA ? 'inativa' : 'instavel');
  }

  // Muito tempo sem resposta valida com a pagina visivel. Oculta, quem segura
  // a consulta e o navegador, e isso nao e falta do motor.
  function frescor() {
    if (document.hidden || !desde) return;
    var idade = Date.now() - (ultimaValida || desde);
    if (idade > INATIVA_MS) definir('inativa');
    else if (idade > INSTAVEL_MS && situacao !== 'inativa') definir('instavel');
  }

  function marcarOculto() {
    document.documentElement.setAttribute('data-stt-oculto', String(document.hidden));
  }

  function montar() {
    if (montado) return;
    texto = $('estado-texto');
    linha = document.querySelector('.estado-linha');
    if (!texto || !linha || !window.MatrizDePontos) return;
    matriz = document.createElement('span');
    matriz.className = 't-matrix stt-espera-matriz';
    matriz.id = 'stt-espera-matriz';
    matriz.setAttribute('aria-hidden', 'true');
    matriz.hidden = true;
    linha.insertBefore(matriz, texto);
    window.MatrizDePontos.montar(matriz);
    linha.insertAdjacentElement('afterend', criarIndicador(false));
    $('motor-parou').appendChild(criarIndicador(true));
    montado = true;
    var observador = new MutationObserver(pintar);
    observador.observe($('start'), { attributes: true, attributeFilter: ['data-estado'] });
    observador.observe($('avisos'), { childList: true });
    observador.observe($('motor-parou'), { attributes: true, attributeFilter: ['hidden'] });
    marcarOculto();
    pintar();
    setInterval(frescor, 1000);
  }

  // A linha so apresenta: um defeito nela nao pode derrubar a consulta de
  // estado que a chama, nem somar uma falha que o motor nao teve.
  function protegido(f) {
    return function () {
      try {
        return f.apply(null, arguments);
      } catch (erro) {
        if (window.console) console.error(erro);
        return undefined;
      }
    };
  }

  // De volta a vista, o resultado antigo nao acende o verde: a linha espera a
  // proxima resposta.
  document.addEventListener('visibilitychange', protegido(function () {
    marcarOculto();
    if (document.hidden) return;
    desde = Date.now();
    ultimaValida = 0;
    falhas = 0;
    tempo = null;
    definir('verificando');
  }));

  window.STTEspera = {
    iniciarConsulta: protegido(iniciarConsulta),
    resposta: protegido(resposta),
    falha: protegido(falha),
    atualizar: protegido(pintar),
    valido: valido
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', protegido(montar));
  else protegido(montar)();
})();
