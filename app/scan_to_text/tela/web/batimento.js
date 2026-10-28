/* Batimento da pagina, num Worker dedicado.

   No modo navegador o motor encerra quando o batimento para. Um navegador
   segura os temporizadores de uma aba oculta ou minimizada (ate um disparo
   por minuto), e o motor leria a janela minimizada como janela fechada. O
   temporizador de um Worker dedicado nao sofre esse aperto. */
var cfg = null;

function pulso() {
  fetch(cfg.url, {
    method: 'POST',
    headers: { 'X-Token': cfg.token, 'Content-Type': 'application/json' },
    body: '{}',
    cache: 'no-store'
  }).catch(function () {});
}

self.onmessage = function (ev) {
  var primeiro = cfg === null;
  cfg = ev.data;
  if (primeiro) {
    pulso();
    setInterval(pulso, cfg.intervalo);
  }
};
