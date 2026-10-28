/* Scan-to-Text: a tela (home e configuracoes).

   Todo texto visivel vem de i18n/<idioma>.json, pela funcao t(); o que vem
   do motor (caminho, linha de log, versao) entra por textContent, nunca como
   HTML. O token chega no fragmento da URL (#t=...), vai para a
   sessionStorage e sai da barra de endereco. */
(function () {
  'use strict';

  var CAMPOS = ['origens', 'destinos', 'idiomas_documentos', 'ping_min', 'days_back',
    'estabilidade_s', 'ocioso_min'];
  var RODANDO = { ocioso: true, trabalhando: true };
  // Avisos que param a leitura (ou parte dela): vêm primeiro, com outra cor e
  // outro ícone. Os de pausa trocam também a frase de estado.
  var AVISO_BLOQUEIA = { disco_cheio: true, destino_inacessivel: true, origem_inacessivel: true,
    leitor_indisponivel: true, 'aviso.vcpp_ausente': true, 'aviso.windows_nao_suportado': true };
  var AVISO_PAUSA = { disco_cheio: true, destino_inacessivel: true, leitor_indisponivel: true };
  // O motor diz qual das duas pastas fica dentro da outra, e não qual é a
  // nova: quando a nova é a de fora, o texto é outro.
  var ERRO_DA_PASTA_NOVA = {
    'origens:destino_em_origem': 'origem_contem_destino',
    'destinos:origem_em_destino': 'destino_contem_origem'
  };
  // Clique duplo não pode ligar e desligar: depois de cada troca de estado
  // vinda do motor, o START fica surdo por este tempo.
  var CARENCIA_MS = 600;
  var SVGNS = 'http://www.w3.org/2000/svg';
  var ICONES = {
    pasta: 'M3 7.5A1.5 1.5 0 0 1 4.5 6h4.2l2 2h8.8A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z',
    fechar: 'M6.5 6.5l11 11M17.5 6.5l-11 11',
    aviso: 'M12 3.2 2.6 19.8h18.8zM12 9.6v4.6M12 16.9v.3',
    pare: 'M8.3 2.8h7.4l5.5 5.5v7.4l-5.5 5.5H8.3l-5.5-5.5V8.3zM9 9l6 6M15 9l-6 6'
  };

  // A linha de conexão (status-espera.js) só apresenta a consulta de estado;
  // sem ela, a consulta segue igual.
  var NADA = function () {};
  var ESPERA = window.STTEspera || { iniciarConsulta: NADA, resposta: NADA, falha: NADA, atualizar: NADA };

  var TOKEN = pegarToken();
  var BASE = {}, TEXTOS = {}, IDIOMAS = [], ATUAL = 'pt-BR';
  var ESTADO = null, falhas = 0, ocupado = false, parado = '', surdoAte = 0;
  var avisoLocal = '', timerAvisoLocal = null, avisosPintados = null;
  var SALVO = null, RASCUNHO = null, salvando = false, barraMsg = null, timerBarra = null;
  var timerLog = null, logPintado = null, focoInicial = false, timerDestaque = null;

  function $(id) { return document.getElementById(id); }
  function cada(lista, f) { Array.prototype.forEach.call(lista, f); }
  function copia(o) { return JSON.parse(JSON.stringify(o)); }

  function pegarToken() {
    var m = /(?:^#|&)t=([^&]+)/.exec(location.hash), t = m ? decodeURIComponent(m[1]) : '';
    try {
      if (t) sessionStorage.setItem('stt_token', t);
      else t = sessionStorage.getItem('stt_token') || '';
    } catch (e) {}
    if (m) history.replaceState(null, '', location.pathname);
    return t;
  }

  function api(metodo, rota, corpo) {
    var op = { method: metodo, headers: { 'X-Token': TOKEN }, cache: 'no-store' };
    if (corpo !== undefined) {
      op.headers['Content-Type'] = 'application/json';
      op.body = JSON.stringify(corpo);
    }
    return fetch('/api/' + rota, op).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) {
          var e = new Error(j.erro || 'http');
          e.codigo = j.erro || ''; e.status = r.status;
          throw e;
        }
        return j;
      });
    });
  }

  function buscarJson(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('i18n');
      return r.json();
    });
  }

  function icone(nome) {
    var s = document.createElementNS(SVGNS, 'svg'), p = document.createElementNS(SVGNS, 'path');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('aria-hidden', 'true');
    s.setAttribute('focusable', 'false');
    p.setAttribute('d', ICONES[nome]);
    s.appendChild(p);
    return s;
  }

  // ─── textos ─────────────────────────────────────────────────────────────

  function valor(dic, chave) {
    return chave.split('.').reduce(function (d, p) {
      return d && typeof d === 'object' ? d[p] : undefined;
    }, dic);
  }

  function t(chave, vars) {
    var v = valor(TEXTOS, chave);
    if (typeof v !== 'string') v = valor(BASE, chave);
    if (typeof v !== 'string') return '';
    if (!vars) return v;
    return v.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? String(vars[k]) : m; });
  }

  window.STTTraduzirStatus = function (chave, vars) { return t('conexao.' + chave, vars); };

  function tl(chave) {
    var v = valor(TEXTOS, chave);
    if (!Array.isArray(v)) v = valor(BASE, chave);
    return Array.isArray(v) ? v : [];
  }

  function aplicarTextos(raiz) {
    raiz = raiz || document;
    cada(raiz.querySelectorAll('[data-i18n]'), function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    cada(raiz.querySelectorAll('[data-i18n-attr]'), function (el) {
      el.getAttribute('data-i18n-attr').split(';').forEach(function (par) {
        var i = par.indexOf(':');
        if (i > 0) el.setAttribute(par.slice(0, i).trim(), t(par.slice(i + 1).trim()));
      });
    });
  }

  function infoIdioma(cod) {
    return IDIOMAS.filter(function (x) { return x.codigo === cod; })[0] || null;
  }

  // Idioma sem tradução mostra o português, com `lang` e `dir` do português:
  // texto em português disposto da direita para a esquerda seria pior.
  function carregarTextos(cod) {
    var info = infoIdioma(cod), usar = info && info.traduzido ? cod : 'pt-BR';
    var pedido = usar === 'pt-BR' ? Promise.resolve(BASE)
      : buscarJson('i18n/' + usar + '.json').catch(function () { usar = 'pt-BR'; return BASE; });
    return pedido.then(function (dic) {
      TEXTOS = dic;
      var rtl = usar !== 'pt-BR' && info && info.rtl;
      document.documentElement.setAttribute('lang', usar);
      document.documentElement.setAttribute('dir', rtl ? 'rtl' : 'ltr');
      $('sem-traducao').hidden = usar === cod;
      aplicarTextos();
      repintar();
    });
  }

  // O motor grava a configuração inteira, e uma origem fora do ar recusa a
  // troca com HTTP 200 e `erros`. A tela fica no idioma escolhido até fechar
  // e avisa que a escolha não ficou gravada.
  function escolherIdioma(cod, forma) {
    if (cod === ATUAL) return;
    ATUAL = cod;
    api('POST', 'config', { idioma_interface: cod })
      .then(function (r) { if (r.erros && r.erros.length) idiomaNaoSalvo(r.erros); })
      .catch(function (err) { idiomaNaoSalvo([err.codigo || 'desconhecido']); });
    carregarTextos(cod).then(function () {
      window.SeletorIdioma.montar(IDIOMAS, ATUAL, escolherIdioma, forma);
    });
  }

  // A home mostra o aviso; a barra das configurações mostra o aviso com os
  // motivos, porque a grade de idiomas fica lá.
  function idiomaNaoSalvo(erros) {
    mostrarAvisoLocal('idioma_nao_salvo');
    mensagemBarra(t('aviso.idioma_nao_salvo'), erros, 8000);
  }

  function repintar() {
    avisosPintados = null; logPintado = null;
    pintarInicio();
    if (parado) mostrarMotorParado(parado);
    if (RASCUNHO) pintarConfig();
    ESPERA.atualizar();
  }

  // ─── home ───────────────────────────────────────────────────────────────

  function escreverEstado(texto, brilho) {
    var el = $('estado-texto');
    if (el.textContent !== texto) el.textContent = texto;
    el.classList.toggle('t-shimmer', brilho);
    if (brilho) el.setAttribute('data-text', texto);
    else el.removeAttribute('data-text');
  }

  function chaveAviso(cod) { return cod.indexOf('aviso.') === 0 ? cod : 'aviso.' + cod; }

  // O motor diz qual pasta está por trás do aviso; sem ela, o texto fala de
  // "uma das pastas".
  function textoAviso(cod) {
    var pastas = (ESTADO && ESTADO.pastas && ESTADO.pastas[cod]) || [];
    var pasta = pastas.length ? pastas.join(', ') : t('aviso.uma_pasta');
    return t(chaveAviso(cod), { pasta: pasta }) || t('aviso.desconhecido', { codigo: cod });
  }

  function pintarAvisos(lista) {
    if (sujo()) lista = lista.concat(['config_pendente']);
    if (avisoLocal) lista = lista.concat([avisoLocal]);
    lista = lista.filter(function (c) { return AVISO_BLOQUEIA[c]; })
      .concat(lista.filter(function (c) { return !AVISO_BLOQUEIA[c]; }));
    var chave = lista.join('|') + JSON.stringify((ESTADO && ESTADO.pastas) || {});
    if (chave === avisosPintados) return;
    avisosPintados = chave;
    var ul = $('avisos');
    ul.textContent = '';
    lista.forEach(function (cod) {
      var li = document.createElement('li'), corpo = document.createElement('span');
      var bloqueia = !!AVISO_BLOQUEIA[cod], acao = t(chaveAviso(cod) + '_acao');
      li.className = bloqueia ? 'aviso bloqueia' : 'aviso';
      li.appendChild(icone(bloqueia ? 'pare' : 'aviso'));
      corpo.textContent = textoAviso(cod);
      if (acao) {
        var sub = document.createElement('span');
        sub.className = 'sub';
        sub.textContent = acao;
        corpo.appendChild(sub);
      }
      li.appendChild(corpo);
      ul.appendChild(li);
    });
  }

  // A conta do dia: a prova de que o app trabalhou, quantos documentos foram
  // para DESCONHECIDO por falta de nome e quantos arquivos não foram lidos.
  function pintarResumo(e, rodando) {
    var h = e.hoje || {}, el = $('resumo'), partes = [];
    if (!rodando && !h.arquivos) { el.hidden = true; return; }
    if (h.documentos) partes.push(t(h.documentos === 1 ? 'inicio.hoje_um' : 'inicio.hoje',
      { documentos: h.documentos, hora: h.ultimo || '' }));
    else if (!h.arquivos) partes.push(t('inicio.hoje_nada'));
    else if (!h.ilegiveis) partes.push(t('inicio.hoje_sem_texto', { hora: h.ultimo || '' }));
    if (h.desconhecidos) partes.push(t(h.desconhecidos === 1 ? 'inicio.desconhecidos_um'
      : 'inicio.desconhecidos', { n: h.desconhecidos }));
    if (h.ilegiveis) partes.push(t(h.ilegiveis === 1 ? 'inicio.ilegiveis_um'
      : 'inicio.ilegiveis', { n: h.ilegiveis }));
    var texto = partes.join('\n');
    if (el.textContent !== texto) el.textContent = texto;
    el.hidden = false;
  }

  function mostrarAvisoLocal(cod) {
    avisoLocal = cod;
    clearTimeout(timerAvisoLocal);
    timerAvisoLocal = setTimeout(function () { avisoLocal = ''; pintarInicio(); }, 6000);
    pintarInicio();
  }

  function pintarInicio() {
    var e = ESTADO;
    if (!e) return;
    var bt = $('start'), est = e.estado || 'parado';
    var rodando = !!RODANDO[est], parando = est === 'parando';
    var pausa = rodando && (e.avisos || []).some(function (c) { return AVISO_PAUSA[c]; });
    // Apertado e esperando o motor, o START fica afundado e azul: o cinza é
    // só de "sem configuração", e mudar para ele debaixo do dedo parece defeito.
    bt.setAttribute('data-estado', e.iniciando ? 'iniciando' : est);
    bt.setAttribute('aria-disabled', (ocupado || parando || (!rodando && !e.configurado)) ? 'true' : 'false');
    $('start-rotulo').textContent = t(rodando || parando ? 'botao.stop' : 'botao.start');
    var chave = parando ? 'estado.parando' : pausa ? 'estado.pausado' : rodando ? 'estado.' + est
      : e.configurado ? 'estado.parado' : 'estado.sem_config';
    if (e.iniciando) chave = 'estado.iniciando';
    escreverEstado(t(chave), est === 'trabalhando' && !pausa);
    $('matrix').hidden = est !== 'trabalhando' || pausa;
    $('calmo').hidden = est !== 'ocioso' && !pausa;
    // O ponto verde diria "tudo certo" ao lado de uma pasta que não abre.
    $('calmo').classList.toggle('alerta', !!(e.avisos && e.avisos.length));
    pintarResumo(e, rodando);
    $('manter').hidden = !rodando;
    // Rascunho não salvo também leva à engrenagem: sem isso, o START
    // continuaria desabilitado sem motivo aparente para quem acabou de
    // escolher as pastas e voltou sem salvar.
    $('ir-config').hidden = !sujo() && (rodando || parando || !!e.configurado);
    pintarAvisos(e.avisos || []);
    var versao = $('versao');
    versao.hidden = !e.versao;
    versao.textContent = e.versao ? t('config.sobre.versao', { versao: e.versao }) : '';
  }

  // START sem configuração: em vez de não fazer nada, mostra o caminho.
  function destacarConfig() {
    var ir = $('ir-config');
    if (ir.hidden) return;
    ir.focus();
    ir.classList.remove('destaque');
    void ir.offsetWidth;
    ir.classList.add('destaque');
    clearTimeout(timerDestaque);
    timerDestaque = setTimeout(function () { ir.classList.remove('destaque'); }, 1700);
  }

  // O segundo clique de um clique duplo (detail 2) é ignorado; o teclado
  // chega com detail 0.
  function alternar(ev) {
    if (!ESTADO || (ev && ev.detail > 1) || performance.now() < surdoAte) return;
    if ($('start').getAttribute('aria-disabled') === 'true') {
      if (!ESTADO.configurado && !RODANDO[ESTADO.estado]) destacarConfig();
      return;
    }
    var rodando = !!RODANDO[ESTADO.estado];
    ocupado = true;
    ESTADO = Object.assign({}, ESTADO, rodando ? { estado: 'parando' } : { iniciando: true });
    pintarInicio();
    api('POST', rodando ? 'stop' : 'start')
      .then(function (e) { receberEstado(e); })
      .catch(function () {
        ESTADO = Object.assign({}, ESTADO, { iniciando: false });
        if (rodando) ESTADO.estado = 'ocioso';
        mostrarAvisoLocal('acao_falhou');
      })
      .then(function () { ocupado = false; pintarInicio(); });
  }

  function rotuloStop(e) { return !!(e && (RODANDO[e.estado] || e.estado === 'parando')); }

  function receberEstado(e) {
    if (ESTADO && rotuloStop(ESTADO) !== rotuloStop(e)) surdoAte = performance.now() + CARENCIA_MS;
    ESTADO = e;
  }

  // Com o aviso do motor na frente, a engrenagem e o Voltar trocariam a vista
  // escondida atrás dele.
  function pintarTopo() {
    var v = document.body.getAttribute('data-vista');
    $('bt-engrenagem').hidden = !!parado || v !== 'inicio';
    $('bt-voltar').hidden = !!parado || v !== 'config';
  }

  function mostrarMotorParado(motivo) {
    parado = motivo;
    document.body.classList.remove('carregando');
    $('motor-parou-texto').textContent = t(motivo === 'acesso' ? 'motor.sem_acesso' : 'motor.parou');
    $('motor-parou').hidden = false;
    pintarTopo();
  }

  function motorVoltou() {
    if (parado === 'parou') {
      parado = '';
      $('motor-parou').hidden = true;
      pintarTopo();
    }
  }

  function perguntarEstado() {
    var inicio = performance.now();
    ESPERA.iniciarConsulta();
    api('GET', 'estado')
      .then(function (e) {
        falhas = 0;
        motorVoltou();
        if (!ocupado) { receberEstado(e); pintarInicio(); }
        // A página só aparece com o primeiro estado: antes dele, o START
        // desenharia um estado que ninguém confirmou.
        document.body.classList.remove('carregando');
        if (!focoInicial) {
          focoInicial = true;
          if (document.activeElement === document.body && !$('vista-inicio').hidden) $('start').focus();
        }
        ESPERA.resposta(e, performance.now() - inicio);
      })
      .catch(function (err) {
        ESPERA.falha(err.status);
        if (err.status === 403) mostrarMotorParado('acesso');
        else if (++falhas >= 3) mostrarMotorParado('parou');
      })
      .then(function () { setTimeout(perguntarEstado, document.hidden ? 5000 : 1000); });
  }

  function batimento() {
    try {
      var w = new Worker('batimento.js');
      w.postMessage({ token: TOKEN, url: '/api/batimento', intervalo: 5000 });
    } catch (e) {
      var pulso = function () { api('POST', 'batimento', {}).catch(function () {}); };
      pulso();
      setInterval(pulso, 5000);
    }
    // sendBeacon não aceita cabeçalho: o token vai no corpo.
    window.addEventListener('pagehide', function () {
      try { navigator.sendBeacon('/api/batimento', JSON.stringify({ token: TOKEN, saindo: true })); } catch (e) {}
    });
  }

  // ─── vistas ─────────────────────────────────────────────────────────────

  function mostrarVista(v) {
    document.body.setAttribute('data-vista', v);
    $('vista-inicio').hidden = v !== 'inicio';
    $('vista-config').hidden = v !== 'config';
    pintarTopo();
    clearInterval(timerLog);
    if (v === 'config') {
      window.scrollTo(0, 0);
      carregarConfig();
      carregarLog();
      timerLog = setInterval(carregarLog, 5000);
      $('config-titulo').focus();
    } else {
      pintarInicio();
      $('bt-engrenagem').focus();
    }
  }

  function abrirConfig() {
    history.pushState({ vista: 'config' }, '');
    mostrarVista('config');
  }

  function voltar() {
    if (history.state && history.state.vista === 'config') history.back();
    else mostrarVista('inicio');
  }

  // ─── configurações ──────────────────────────────────────────────────────

  function carregarConfig() {
    return api('GET', 'config').then(function (c) {
      SALVO = c;
      if (!RASCUNHO) RASCUNHO = copia(c);
      pintarConfig();
    }).catch(function () {});
  }

  function sujo() {
    return !!(SALVO && RASCUNHO) && CAMPOS.some(function (k) {
      return JSON.stringify(RASCUNHO[k]) !== JSON.stringify(SALVO[k]);
    });
  }

  function pintarConfig() {
    if (!RASCUNHO) return;
    pintarLista('origens');
    pintarLista('destinos');
    pintarIdiomasDoc();
    pintarNumeros();
    pintarReferencias();
    pintarBarra();
  }

  function pintarLista(nome) {
    var ul = $('lista-' + nome), itens = RASCUNHO[nome] || [];
    ul.textContent = '';
    if (!itens.length) {
      var vazio = document.createElement('li');
      vazio.className = 'vazio';
      vazio.textContent = t('config.' + nome + '.vazio');
      ul.appendChild(vazio);
      return;
    }
    itens.forEach(function (caminho, i) {
      var li = document.createElement('li'), c = document.createElement('span');
      var rm = document.createElement('button'), rot = document.createElement('span');
      li.className = 'pasta';
      li.appendChild(icone('pasta'));
      c.className = 'caminho';
      c.dir = 'ltr';
      c.textContent = caminho;
      li.appendChild(c);
      rm.type = 'button';
      rm.className = 'remover';
      rm.setAttribute('aria-label', t('config.remover_rotulo', { caminho: caminho }));
      rm.appendChild(icone('fechar'));
      rot.textContent = t('config.remover');
      rm.appendChild(rot);
      rm.addEventListener('click', function () {
        RASCUNHO[nome].splice(i, 1);
        pintarLista(nome);
        editou();
        document.querySelector('[data-adicionar="' + nome + '"]').focus();
      });
      li.appendChild(rm);
      ul.appendChild(li);
    });
  }

  function adicionar(nome, caminho) {
    caminho = (caminho || '').trim();
    if (!caminho) return false;
    var lista = RASCUNHO[nome];
    var repetido = lista.some(function (p) { return p.toLowerCase() === caminho.toLowerCase(); });
    if (repetido) return 'repetido';
    lista.push(caminho);
    pintarLista(nome);
    editou();
    return true;
  }

  function textoErroPasta(nome, cod, caminho) {
    var chave = ERRO_DA_PASTA_NOVA[nome + ':' + cod] || cod;
    return t('erro_pasta.' + chave, { caminho: caminho }) || textoErro(cod);
  }

  // A pasta é conferida pelo motor na hora em que entra na lista: o erro
  // aparece junto dela, e não só ao salvar. Sem resposta do motor, ela entra
  // e a conferência fica para o salvar.
  function incluir(nome, caminho) {
    caminho = (caminho || '').trim();
    if (!caminho) return Promise.resolve({ ok: false, erro: '' });
    var repetido = RASCUNHO[nome].some(function (p) { return p.toLowerCase() === caminho.toLowerCase(); });
    if (repetido) return Promise.resolve({ ok: false, erro: t('config.ja_na_lista') });
    return api('POST', 'conferir-pasta', { caminho: caminho, papel: nome,
      origens: RASCUNHO.origens, destinos: RASCUNHO.destinos })
      .then(function (r) {
        if (r.erros && r.erros.length) {
          return { ok: false, erro: r.erros.map(function (c) { return textoErroPasta(nome, c, caminho); }).join(' ') };
        }
        adicionar(nome, caminho);
        return { ok: true };
      })
      .catch(function () { adicionar(nome, caminho); return { ok: true }; });
  }

  // Sem seletor nativo (modo navegador), abre o painel de colar; cancelado,
  // não abre nada.
  function escolherPasta(nome) {
    api('POST', 'escolher-pasta')
      .then(function (r) {
        if (r.sem_seletor) { abrirPainel(nome, true); return; }
        if (!r.caminho) return;
        if (nome === 'log') { salvarLog(r.caminho); return; }
        incluir(nome, r.caminho).then(function (res) {
          if (!res.ok && res.erro) abrirPainel(nome, false, r.caminho, res.erro);
        });
      })
      .catch(function () { abrirPainel(nome, true); });
  }

  function juntar(base, nome) { return base.replace(/[\\\/]+$/, '') + '\\' + nome; }

  function montarPainel(alvo, nome) {
    alvo.appendChild($('tpl-colar').content.cloneNode(true));
    var input = alvo.querySelector('input'), erro = alvo.querySelector('[data-papel="erro"]');
    var nav = alvo.querySelector('[data-papel="navegador"]'), lista = alvo.querySelector('[data-papel="lista"]');
    var atualEl = alvo.querySelector('[data-papel="atual"]');
    var pai = null;
    input.id = 'colar-' + nome;
    erro.id = input.id + '-erro';
    input.setAttribute('aria-describedby', erro.id);
    alvo.querySelector('label').setAttribute('for', input.id);
    if (nome === 'log') alvo.querySelector('[data-acao="usar"]').setAttribute('data-i18n', 'config.log.destino');
    aplicarTextos(alvo);

    function falharTexto(texto, neutro) {
      erro.textContent = texto || '';
      erro.hidden = !texto;
      erro.classList.toggle('conferindo', !!neutro);
      input.setAttribute('aria-invalid', texto && !neutro ? 'true' : 'false');
    }
    function falhar(chave) { falharTexto(chave ? t(chave) : ''); }
    alvo.mostrarErro = falharTexto;
    function fechar() {
      alvo.hidden = true;
      var volta = document.querySelector('[data-colar="' + nome + '"]') || $('bt-salvar-log');
      volta.focus();
    }
    function usar(caminho) {
      caminho = (caminho || '').trim();
      if (!caminho) return;
      if (nome === 'log') { salvarLog(caminho); fechar(); return; }
      falharTexto(t('config.conferindo'), true);
      incluir(nome, caminho).then(function (res) {
        if (!res.ok) { falharTexto(res.erro); input.focus(); return; }
        input.value = '';
        falhar('');
        fechar();
      });
    }
    // Navegar põe a pasta atual no campo: o mesmo "Usar esta pasta" serve
    // para o caminho colado e para a pasta escolhida na lista.
    function navegar(caminho) {
      api('POST', 'pastas', { caminho: caminho || null }).then(function (r) {
        pai = r.pai;
        nav.hidden = false;
        atualEl.textContent = r.caminho || t('config.unidades');
        atualEl.dir = 'ltr';
        if (r.caminho) input.value = r.caminho;
        alvo.querySelector('[data-acao="acima"]').disabled = r.pai === null;
        lista.textContent = '';
        if (!r.pastas.length) {
          var vazio = document.createElement('li');
          vazio.className = 'vazio';
          vazio.textContent = t('config.sem_subpastas');
          lista.appendChild(vazio);
        }
        r.pastas.forEach(function (n) {
          var li = document.createElement('li'), b = document.createElement('button');
          var rot = document.createElement('span');
          b.type = 'button';
          b.setAttribute('data-acao', 'entrar');
          b.setAttribute('data-caminho', r.caminho ? juntar(r.caminho, n) : n);
          b.appendChild(icone('pasta'));
          rot.textContent = (!r.caminho && r.rotulos && r.rotulos[n]) || n;
          rot.dir = 'ltr';
          b.appendChild(rot);
          if (!r.caminho && r.nuvem && r.nuvem.indexOf(n) >= 0) {
            var tag = document.createElement('span');
            tag.className = 'etiqueta-nuvem';
            tag.textContent = t('config.nuvem');
            b.appendChild(tag);
          }
          li.appendChild(b);
          lista.appendChild(li);
        });
        falhar('');
        // A lista foi trocada inteira: o foco volta para dentro dela, e não
        // para o corpo da página.
        var primeiro = lista.querySelector('button');
        (primeiro || atualEl).focus();
      }).catch(function () { falhar('config.pasta_inacessivel'); });
    }

    alvo.addEventListener('click', function (ev) {
      var bt = ev.target.closest('[data-acao]');
      if (!bt || !alvo.contains(bt)) return;
      var acao = bt.getAttribute('data-acao');
      if (acao === 'usar') usar(input.value);
      else if (acao === 'procurar') navegar(input.value.trim());
      else if (acao === 'fechar') fechar();
      else if (acao === 'acima') navegar(pai || '');
      else if (acao === 'entrar') navegar(bt.getAttribute('data-caminho'));
    });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); usar(input.value); }
      else if (ev.key === 'Escape') { ev.preventDefault(); fechar(); }
    });
  }

  function abrirPainel(nome, semSeletor, caminho, erroTexto) {
    var alvo = document.querySelector('[data-painel="' + nome + '"]');
    if (!alvo.firstElementChild) montarPainel(alvo, nome);
    alvo.querySelector('[data-papel="sem-seletor"]').hidden = !semSeletor;
    var input = alvo.querySelector('input');
    if (caminho) input.value = caminho;
    alvo.mostrarErro(erroTexto || '');
    alvo.hidden = false;
    input.focus();
  }

  // Só idioma com pacote de regras serve de idioma de documento; o que já
  // está gravado continua na lista, para a pessoa ver e trocar.
  function idiomasDeDocumento(docs) {
    return IDIOMAS.filter(function (x) { return x.regras !== false || docs.indexOf(x.codigo) >= 0; });
  }

  function pintarIdiomasDoc() {
    var sel = $('idioma-doc'), docs = RASCUNHO.idiomas_documentos || ['pt-BR'];
    var grade = $('idiomas-extras'), opcoes = idiomasDeDocumento(docs);
    var extras = opcoes.filter(function (x) { return x.codigo !== docs[0]; });
    sel.textContent = '';
    opcoes.forEach(function (x) {
      var o = document.createElement('option');
      o.value = x.codigo;
      o.textContent = x.nome;
      o.lang = x.codigo;
      o.dir = x.rtl ? 'rtl' : 'ltr';
      sel.appendChild(o);
    });
    sel.value = docs[0];
    // Com um idioma só de documento, a escolha é uma frase, e não um controle.
    // Idioma gravado fora das opções (código antigo, editado à mão) deixa o
    // controle à vista e vazio, para a escolha de um idioma que existe.
    var atual = opcoes.filter(function (x) { return x.codigo === docs[0]; })[0];
    var unico = opcoes.length <= 1 && !!atual;
    $('campo-idioma-doc').hidden = unico;
    $('campo-extras').hidden = unico || !atual;
    $('idioma-unico').hidden = !unico;
    $('idioma-unico').textContent = unico ? t('config.idiomas.unico', { idioma: atual.nome }) : '';
    grade.textContent = '';
    grade.hidden = !extras.length;
    $('extras-nota').hidden = !extras.length;
    $('extras-vazio').hidden = !!extras.length;
    extras.forEach(function (x) {
      var rot = document.createElement('label'), cb = document.createElement('input');
      var nomeEl = document.createElement('span');
      rot.className = 'marcar';
      cb.type = 'checkbox';
      cb.value = x.codigo;
      cb.checked = docs.indexOf(x.codigo) > 0;
      nomeEl.textContent = x.nome;
      nomeEl.lang = x.codigo;
      nomeEl.dir = x.rtl ? 'rtl' : 'ltr';
      rot.appendChild(cb);
      rot.appendChild(nomeEl);
      grade.appendChild(rot);
    });
  }

  function lerIdiomasDoc() {
    var principal = $('idioma-doc').value, marcados = {};
    cada($('idiomas-extras').querySelectorAll('input:checked'), function (cb) { marcados[cb.value] = true; });
    var extras = IDIOMAS.map(function (x) { return x.codigo; }).filter(function (c) {
      return c !== principal && marcados[c];
    });
    RASCUNHO.idiomas_documentos = [principal].concat(extras);
  }

  // Os limites são os do config.py (LIMITES): campo com `data-decimal` aceita
  // fração, como o config.json aceita 0.5 minuto.
  function validarNumero(inp) {
    var minimo = Number(inp.getAttribute('data-minimo')), txt = inp.value.trim();
    var decimal = inp.hasAttribute('data-decimal'), forma = decimal ? /^(\d+|\d*\.\d+)$/ : /^\d+$/;
    var ok = forma.test(txt) && Number(txt) >= minimo, erro = $(inp.id + '-erro');
    var vars = { minimo: minimo.toLocaleString(document.documentElement.lang || 'pt-BR') };
    inp.setAttribute('aria-invalid', ok ? 'false' : 'true');
    erro.hidden = ok;
    erro.textContent = ok ? '' : decimal ? t('config.leitura.invalido_decimal', vars)
      : t('config.leitura.invalido', vars);
    if (ok) RASCUNHO[inp.getAttribute('data-campo')] = Number(txt);
    return ok;
  }

  function pintarNumeros() {
    cada(document.querySelectorAll('input[data-campo]'), function (inp) {
      if (document.activeElement !== inp) inp.value = RASCUNHO[inp.getAttribute('data-campo')];
      validarNumero(inp);
    });
  }

  function invalidos() {
    return document.querySelectorAll('input[data-campo][aria-invalid="true"]').length > 0;
  }

  function pintarReferencias() {
    var dl = $('referencias');
    dl.textContent = '';
    tl('config.referencias.itens').forEach(function (item) {
      var dt = document.createElement('dt'), dd = document.createElement('dd');
      var lic = document.createElement('span');
      dt.textContent = item.nome;
      dd.textContent = item.papel;
      lic.textContent = item.licenca;
      dd.appendChild(lic);
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
  }

  function textoErro(cod) {
    return t('erro.' + cod) || t('erro.desconhecido', { codigo: cod });
  }

  function pintarBarra() {
    var s = sujo(), ul = $('salvar-erros');
    var msg = barraMsg ? barraMsg.texto
      : (s ? t(invalidos() ? 'config.corrigir_campo' : 'config.pendente') : '');
    $('salvar-msg').textContent = salvando ? t('config.salvando') : msg;
    ul.textContent = '';
    ((barraMsg && barraMsg.erros) || []).forEach(function (cod) {
      var li = document.createElement('li');
      li.textContent = textoErro(cod);
      ul.appendChild(li);
    });
    $('barra-salvar').hidden = !s && !barraMsg && !salvando;
    $('bt-salvar').disabled = !s || salvando || invalidos();
    $('bt-descartar').hidden = !s || salvando;
  }

  // Mexer no rascunho apaga a mensagem da última tentativa de salvar: ela
  // falava de outro rascunho.
  function editou() {
    barraMsg = null;
    clearTimeout(timerBarra);
    pintarBarra();
  }

  function mensagemBarra(texto, erros, ms) {
    barraMsg = { texto: texto, erros: erros || [] };
    clearTimeout(timerBarra);
    if (ms) timerBarra = setTimeout(function () { barraMsg = null; pintarBarra(); }, ms);
    pintarBarra();
  }

  function salvar() {
    if (!sujo() || invalidos() || salvando) return;
    var corpo = {};
    CAMPOS.forEach(function (k) { corpo[k] = RASCUNHO[k]; });
    salvando = true;
    barraMsg = null;
    pintarBarra();
    api('POST', 'config', corpo)
      .then(function (r) {
        salvando = false;
        if (r.erros && r.erros.length) {
          mensagemBarra(t('config.nao_salvou'), r.erros);
          return;
        }
        SALVO = r.config;
        RASCUNHO = copia(SALVO);
        pintarConfig();
        mensagemBarra(t('config.salvo'), [], 2500);
        // O botão de salvar fica desabilitado: o foco volta ao título.
        $('config-titulo').focus();
      })
      .catch(function (err) {
        salvando = false;
        mensagemBarra(t('config.nao_salvou'), [err.codigo || 'desconhecido']);
      });
  }

  function descartar() {
    RASCUNHO = copia(SALVO);
    barraMsg = null;
    pintarConfig();
    $('config-titulo').focus();
  }

  function carregarLog() {
    api('GET', 'log').then(function (r) {
      var linhas = r.linhas || [], ol = $('log'), chave = linhas.join('\n');
      if (chave === logPintado) return;
      logPintado = chave;
      var noFim = ol.scrollTop + ol.clientHeight >= ol.scrollHeight - 4;
      ol.textContent = '';
      if (!linhas.length) {
        var vazio = document.createElement('li');
        vazio.className = 'vazio';
        vazio.textContent = t('config.log.vazio');
        ol.appendChild(vazio);
      }
      linhas.forEach(function (l) {
        var li = document.createElement('li');
        li.textContent = l;
        ol.appendChild(li);
      });
      if (noFim) ol.scrollTop = ol.scrollHeight;
    }).catch(function () {});
  }

  function retornoLog(texto, tipo) {
    var el = $('log-retorno');
    el.textContent = texto;
    el.className = 'retorno' + (tipo ? ' ' + tipo : '');
  }

  function salvarLog(destino) {
    retornoLog('', '');
    api('POST', 'salvar-log', { destino: destino })
      .then(function (r) { retornoLog(t('config.log.salvo', { arquivo: r.arquivo }), 'ok'); })
      .catch(function () { retornoLog(t('config.log.falhou'), 'falhou'); });
  }

  // ─── início ─────────────────────────────────────────────────────────────

  function ligar() {
    $('start').addEventListener('click', alternar);
    $('bt-engrenagem').addEventListener('click', abrirConfig);
    $('ir-config').addEventListener('click', abrirConfig);
    $('bt-voltar').addEventListener('click', voltar);
    window.addEventListener('popstate', function (ev) {
      mostrarVista(ev.state && ev.state.vista === 'config' ? 'config' : 'inicio');
    });
    cada(document.querySelectorAll('[data-adicionar]'), function (bt) {
      bt.addEventListener('click', function () { escolherPasta(bt.getAttribute('data-adicionar')); });
    });
    cada(document.querySelectorAll('[data-colar]'), function (bt) {
      bt.addEventListener('click', function () { abrirPainel(bt.getAttribute('data-colar')); });
    });
    $('bt-salvar-log').addEventListener('click', function () { escolherPasta('log'); });
    $('idioma-doc').addEventListener('change', function () {
      lerIdiomasDoc();
      pintarIdiomasDoc();
      editou();
    });
    $('idiomas-extras').addEventListener('change', function () { lerIdiomasDoc(); editou(); });
    cada(document.querySelectorAll('input[data-campo]'), function (inp) {
      inp.addEventListener('input', function () { validarNumero(inp); editou(); });
      inp.addEventListener('blur', function () { if (validarNumero(inp)) inp.value = RASCUNHO[inp.getAttribute('data-campo')]; });
    });
    $('bt-salvar').addEventListener('click', salvar);
    $('bt-descartar').addEventListener('click', descartar);
  }

  // Sem motor na primeira carga, a página tenta de novo sozinha: o motor pode
  // estar subindo ainda.
  function carregarInicio() {
    buscarJson('i18n/pt-BR.json')
      .then(function (base) {
        BASE = base;
        TEXTOS = BASE;
        return api('GET', 'idiomas');
      })
      .then(function (r) {
        IDIOMAS = r.idiomas;
        ATUAL = r.atual || 'pt-BR';
        parado = '';
        $('motor-parou').hidden = true;
        pintarTopo();
        return carregarTextos(ATUAL).then(function () {
          window.SeletorIdioma.montar(IDIOMAS, ATUAL, escolherIdioma);
          perguntarEstado();
        });
      })
      .catch(function (e) {
        ESPERA.falha(e.status);
        aplicarTextos();
        mostrarMotorParado(e.status === 403 ? 'acesso' : 'parou');
        if (e.status !== 403) setTimeout(carregarInicio, 3000);
      });
  }

  // O batimento e o aviso de saída ligam antes da carga dos textos: no modo
  // navegador, fechar a janela durante a carga também precisa encerrar o motor.
  function iniciar() {
    batimento();
    ligar();
    window.MatrizDePontos.montar($('matrix'));
    carregarInicio();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
