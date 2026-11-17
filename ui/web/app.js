/* ==========================================================================
   JARVIS · Lógica de la interfaz
   Habla con el núcleo por la API local (/api/...) y recibe en directo lo que
   ocurre por un canal de eventos (/api/eventos).
   Todo el texto que viene de fuera (modelo, Internet, memoria) se pinta con
   textContent: nunca se interpreta como HTML.
   ========================================================================== */
"use strict";
(() => {

// --------------------------------------------------------------------------
// Utilidades
// --------------------------------------------------------------------------
const TEXTOS = window.TEXTOS || {};
const t = (clave, datos = {}) => {
  let texto = TEXTOS[clave] ?? clave;
  for (const [nombre, valor] of Object.entries(datos)) texto = texto.replaceAll(`{${nombre}}`, valor);
  return texto;
};
const $ = (selector, raiz = document) => raiz.querySelector(selector);
const $$ = (selector, raiz = document) => [...raiz.querySelectorAll(selector)];
const SVG = "http://www.w3.org/2000/svg";

function el(etiqueta, propiedades = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  for (const [nombre, valor] of Object.entries(propiedades)) {
    if (valor == null || valor === false) continue;
    if (nombre === "class") nodo.className = valor;
    else if (nombre === "text") nodo.textContent = valor;
    else if (nombre.startsWith("on")) nodo.addEventListener(nombre.slice(2), valor);
    else nodo.setAttribute(nombre, valor === true ? "" : valor);
  }
  for (const hijo of hijos.flat()) if (hijo != null && hijo !== false) nodo.append(hijo);
  return nodo;
}

function icono(nombre, clase = "icono") {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", clase);
  const uso = document.createElementNS(SVG, "use");
  uso.setAttribute("href", `#i-${nombre}`);
  svg.append(uso);
  return svg;
}

async function api(metodo, ruta, cuerpo) {
  const respuesta = await fetch(ruta, {
    method: metodo,
    credentials: "same-origin",
    headers: cuerpo !== undefined ? { "Content-Type": "application/json" } : {},
    body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined,
  });
  let datos = {};
  try { datos = await respuesta.json(); } catch { /* respuesta sin cuerpo */ }
  if (!respuesta.ok) {
    const error = new Error(datos.error || t("comun.error"));
    error.estado = respuesta.status;
    error.datos = datos;
    throw error;
  }
  return datos;
}

function aviso(texto, tipo = "", duracion = 4200) {
  const nodo = el("div", { class: `aviso ${tipo}`, text: texto });
  $("#avisos").append(nodo);
  setTimeout(() => nodo.remove(), duracion);
}

function haceCuanto(segundos) {
  const pasado = Date.now() / 1000 - segundos;
  if (pasado < 60) return t("tiempo.ahora");
  if (pasado < 3600) return t("tiempo.minutos", { n: Math.floor(pasado / 60) });
  if (pasado < 86400) return t("tiempo.horas", { n: Math.floor(pasado / 3600) });
  if (pasado < 172800) return t("tiempo.ayer");
  return t("tiempo.dias", { n: Math.floor(pasado / 86400) });
}
const fechaCorta = (segundos) => new Date(segundos * 1000).toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });
const horaCorta = (segundos) => new Date(segundos * 1000).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
const rebote = (funcion, espera = 280) => { let reloj; return (...a) => { clearTimeout(reloj); reloj = setTimeout(() => funcion(...a), espera); }; };

// --------------------------------------------------------------------------
// Estado de la interfaz
// --------------------------------------------------------------------------
const E = {
  vista: "inicio",
  modo: "texto",
  estado: "reposo",
  usuario: "",
  salud: {},
  seguridad: { modo: "confirmar", configurada: false },
  ajustes: null,
  herramientas: [],
  mensajes: new Map(),      // id -> nodo del mensaje
  categoria: "",
  conversacion: null,
  tituloConversacion: "",
  pendiente: null,
  nivel: 0,
  conectado: false,
  huboCorte: false,
};

const ICONO_HERRAMIENTA = { apps: "app", busqueda: "buscar", tiempo: "sol", escritura: "teclado", comandos: "terminal", cerrar: "cerrar", teclas: "teclado", musica: "musica", memoria: "memoria", recordatorios: "reloj", equipo: "info", pantalla: "app", archivos: "tarea", mcp: "herramientas" };
const ICONO_RECORDATORIO = { temporizador: "reloj", alarma: "reloj", recordatorio: "tarea", cita: "tarea" };
const ORIGEN_RECUERDO = { sugerido: "recuerdos.sugerido", aprendido: "recuerdos.aprendido" };
const ICONO_CATEGORIA = { personales: "usuario", preferencias: "corazon", "información": "info", tareas: "tarea" };
const CLASE_SALUD = { activa: "bien", escuchando: "bien", hablando: "bien", conectado: "bien", disponible: "bien", apagada: "apagado", sin_conexion: "mal", sin_modelo: "mal", sin_microfono: "mal", error: "mal" };

// --------------------------------------------------------------------------
// Arranque
// --------------------------------------------------------------------------
async function iniciar() {
  crearEstrellas();
  enlazar();
  arrancarReloj();
  arrancarOnda();
  await sincronizar();
  conectarEventos();
}

async function sincronizar() {
  try {
    const datos = await api("GET", "/api/estado");
    E.usuario = datos.usuario || "";
    E.modo = datos.modo;
    E.estado = datos.estado;
    E.salud = datos.salud;
    E.seguridad = datos.seguridad;
    E.herramientas = datos.herramientas;
    E.ajustes = datos.ajustes;
    E.conversacion = datos.conversacion;
    E.recuerdos = datos.recuerdos;
    pintarSaludo();
    pintarModo();
    pintarEstado();
    pintarSalud();
    pintarAcciones(datos.acciones);
    pintarConversacion(datos.mensajes);
    mostrarPendiente(datos.pendiente);
    if (datos.primera_vez) mostrarBienvenida();
  } catch (error) {
    aviso(error.message, "error");
  }
}

function conectarEventos() {
  const fuente = new EventSource("/api/eventos");
  fuente.onopen = () => {
    marcarConexion(true);
    if (E.huboCorte) { E.huboCorte = false; sincronizar(); }      // tras un corte, se vuelve a pedir todo
  };
  fuente.onerror = () => { marcarConexion(false); E.huboCorte = true; };
  fuente.onmessage = (mensaje) => {
    let evento;
    try { evento = JSON.parse(mensaje.data); } catch { return; }
    const manejador = EVENTOS[evento.tipo];
    if (manejador) manejador(evento.datos || {});
  };
}

function marcarConexion(bien) {
  E.conectado = bien;
  $("#conexion").classList.toggle("caida", !bien);
  $("#conexion-texto").textContent = t(bien ? "conexion.bien" : "conexion.mal");
}

// --------------------------------------------------------------------------
// Eventos que llegan del núcleo
// --------------------------------------------------------------------------
const EVENTOS = {
  estado(datos) { E.estado = datos.valor || "reposo"; pintarEstado(); },
  mensaje(datos) { pintarMensaje(datos); subtitular(datos); },
  fragmento(datos) { pintarFragmento(datos.id, datos.texto); },
  confirmacion(datos) { mostrarPendiente(datos.activa ? { tipo: "accion", ...datos } : null, "accion"); },
  sugerencia(datos) { mostrarPendiente(datos.activa ? { tipo: "recuerdo", ...datos } : null, "recuerdo"); },
  accion(datos) { anadirAccion(datos); },
  sistema(datos) { E.salud[datos.componente] = datos.estado; pintarSalud(); },
  nivel(datos) { E.nivel = datos.valor || 0; },
  recuerdos() {
    if (E.vista === "memoria") cargarRecuerdos();
    else api("GET", "/api/recuerdos").then((datos) => pintarTelemetria(datos.total)).catch(() => {});
  },
  aviso(datos) { aviso(datos.texto, "recordatorio", 12000); },
  aprendido(datos) {
    for (const texto of datos.textos || []) aviso(t("recuerdos.aprendido.aviso", { texto }), "aprendido", 7000);
  },
  historial() { if (E.vista === "chat") cargarHistorial(); },
  conversacion(datos) {
    const cambia = datos.id !== E.conversacion;
    E.conversacion = datos.id;
    if (datos.id == null) { E.tituloConversacion = ""; pintarConversacion([]); }
    if (cambia && E.vista === "chat") cargarHistorial();
  },
  async ajustes(datos) {
    if (datos.seguridad) E.seguridad = datos.seguridad;
    try {
      E.ajustes = await api("GET", "/api/ajustes");
      E.modo = E.ajustes.valores.modo;
      E.usuario = E.ajustes.valores.usuario.nombre;
      E.herramientas = E.herramientas.map((h) => ({ ...h, activa: E.ajustes.valores.herramientas[h.id] !== false }));
      pintarModo(); pintarSaludo(); pintarEstado();
      if (E.vista === "ajustes") pintarAjustes();
      if (E.vista === "herramientas") pintarHerramientas();
    } catch { /* se reintentará con el siguiente cambio */ }
  },
  apagar() {
    abrirModal([el("h2", {}, icono("chispa"), t("apagado"))]);
    setTimeout(() => window.close(), 900);
  },
};

// --------------------------------------------------------------------------
// Inicio: saludo, modo, estado, reloj, estrellas y onda
// --------------------------------------------------------------------------
function pintarSaludo() {
  $("#saludo").textContent = E.usuario ? t("saludo", { nombre: E.usuario }) : t("saludo.sin_nombre");
}

function pintarModo() {
  for (const boton of $$(".modo-boton")) boton.classList.toggle("activo", boton.dataset.modo === E.modo);
  pintarTelemetria();
}

/* Lecturas de las esquinas del HUD: modelo en uso, recuerdos guardados y modo. */
function pintarTelemetria(recuerdos) {
  if (recuerdos != null) E.recuerdos = recuerdos;
  const modelo = E.ajustes?.valores?.ia?.modelo;
  $("#tele-modelo").textContent = modelo ? modelo.toUpperCase() : "—";
  $("#tele-recuerdos").textContent = E.recuerdos == null ? "—" : `${E.recuerdos} ${E.recuerdos === 1 ? "RECUERDO" : "RECUERDOS"}`;
  $("#tele-modo").textContent = E.modo === "voz" ? "VOZ" : "TEXTO";
}

function pintarEstado() {
  const estado = E.estado;
  $("#orbe").dataset.estado = estado;
  const ocupado = estado === "procesando" || estado === "hablando";
  let texto = t(`estado.${estado}`);
  if (estado === "reposo") texto = t(E.modo === "voz" ? "estado.voz_espera" : "estado.texto_espera");
  $("#estado-texto").textContent = texto;
  $("#chat-estado").textContent = t(`estado.${estado}`);

  // El botón de enviar se convierte en «detener» mientras JARVIS piensa o habla.
  for (const boton of $$('[data-accion="enviar"]')) {
    boton.classList.toggle("parar", ocupado);
    boton.title = t(ocupado ? "chat.detener" : "chat.enviar");
    boton.setAttribute("aria-label", boton.title);
    boton.replaceChildren(icono(ocupado ? "detener" : "enviar"));
  }
  for (const boton of $$('[data-accion="microfono"]')) boton.classList.toggle("activo", estado === "escuchando");
  if (estado === "procesando") mostrarEscribiendo(); else if (estado !== "hablando") quitarEscribiendo();
  pintarSalud();
}

function pintarSalud() {
  const valores = { ...E.salud };
  if (E.estado === "hablando") valores.voz = "hablando";
  for (const componente of ["ia", "voz", "internet", "memoria"]) {
    const nodo = $(`#salud-${componente}`);
    const estado = valores[componente] || "";
    nodo.textContent = t(`salud.${componente}.${estado}`);
    nodo.className = CLASE_SALUD[estado] || "espera";
  }
}

function arrancarReloj() {
  const pintar = () => {
    const ahora = new Date();
    $("#hora").textContent = ahora.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
    const dia = ahora.toLocaleDateString("es-ES", { weekday: "short" }).replace(".", "");
    const resto = ahora.toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric" });
    $("#fecha").textContent = `${dia.charAt(0).toUpperCase()}${dia.slice(1)}, ${resto}`;
  };
  pintar();
  setInterval(pintar, 1000);
  setInterval(() => { for (const nodo of $$("[data-fecha]")) nodo.textContent = haceCuanto(Number(nodo.dataset.fecha)); }, 30000);
}

function crearEstrellas() {
  const cielo = $("#cielo");
  let semilla = 20250524;
  const azar = () => { semilla = (semilla * 1664525 + 1013904223) % 4294967296; return semilla / 4294967296; };
  // Partículas de luz del HUD: pocas y dispersas, algunas parpadean.
  for (let i = 0; i < 42; i++) {
    const tamano = azar() < .8 ? 1.5 : 2.5;
    const particula = el("i");
    particula.style.cssText = `left:${(azar() * 100).toFixed(2)}%;top:${(azar() * 100).toFixed(2)}%;width:${tamano}px;height:${tamano}px;` +
      `opacity:${(.15 + azar() * .5).toFixed(2)}`;
    cielo.append(particula);
  }
}

function arrancarOnda() {
  /* Motor de animación del HUD. Todo lo que se mueve de forma continua (anillos del reactor,
     onda y parpadeos) sale de este único temporizador: unos 12 fotogramas por segundo cuando
     JARVIS escucha, piensa o habla, y unos 9 en reposo.
     Con animaciones CSS, la ventana nativa (que compone por software) consumía un núcleo
     entero de procesador. Además, sin nadie mirando el HUD se queda quieto (ver fotograma). */
  const lienzo = $("#onda");
  const contexto = lienzo.getContext("2d");
  const orbe = $("#orbe");
  const BARRAS = 56;
  const INTERVALO = 80;               // ms entre fotogramas con JARVIS activo
  const INTERVALO_REPOSO = 110;       // y en reposo: el giro lento no necesita más
  const alturas = new Float32Array(BARRAS);
  // capa -> grados por segundo según el estado (sentido negativo = antihorario)
  const anillos = [
    { nodo: $(".r-marcas", orbe), angulo: 0, velocidad: () => 2.6 },
    { nodo: $(".r-arcos", orbe), angulo: 0, velocidad: () => ({ escuchando: 51, procesando: 90, hablando: 36 }[E.estado] || 18) },
    { nodo: $(".r-segmentos", orbe), angulo: 0, velocidad: () => (E.estado === "procesando" ? -60 : -9.5) },
    { nodo: $(".r-nodos", orbe), angulo: 0, velocidad: () => 12.9 },
    { nodo: $(".r-interior", orbe), angulo: 0, velocidad: () => -22.5 },
    { nodo: $(".r-barrido", orbe), angulo: 0, velocidad: () => (E.estado === "procesando" ? 240 : 0) },
  ];
  let nivel = 0, ultimoPulso = "", linea = null, anchoLinea = 0, anterior = performance.now(), ultimoLatido = 0;
  let firmaQuieta = "";        // en pausa se pinta un único fotograma quieto; se repinta solo si algo cambia

  function ajustar() {
    const escala = window.devicePixelRatio || 1;
    const caja = lienzo.getBoundingClientRect();
    if (!caja.width) return false;
    lienzo.width = Math.round(caja.width * escala);
    lienzo.height = Math.round(caja.height * escala);
    contexto.setTransform(escala, 0, 0, escala, 0, 0);
    linea = null;
    return true;
  }
  window.addEventListener("resize", ajustar);

  function fotograma() {
    const ahora = performance.now();
    let transcurrido = Math.min(.25, (ahora - anterior) / 1000);
    anterior = ahora;
    // Sin nadie mirando no se anima nada: ventana minimizada, o en segundo plano con JARVIS en
    // reposo. Así no gasta procesador mientras trabajas en otra cosa; en cuanto le hablas
    // (escucha, piensa o habla) o vuelves a su ventana, el HUD se reanima.
    if (document.hidden) { firmaQuieta = ""; return; }
    const enPausa = E.estado === "reposo" && !document.hasFocus();
    if (enPausa) {
      const firma = `${E.vista}|${lienzo.clientWidth}|${E.modo}`;
      if (firma === firmaQuieta) return;               // el fotograma quieto ya está pintado
      firmaQuieta = firma;
      transcurrido = 0;                                // los anillos se quedan donde estaban
      document.documentElement.dataset.latido = "0";   // indicadores encendidos, sin parpadeo
    } else {
      firmaQuieta = "";
      // Parpadeo común (punto de conexión, estados «en espera», indicador de escritura).
      if (ahora - ultimoLatido > 640) {
        ultimoLatido = ahora;
        const raiz = document.documentElement;
        raiz.dataset.latido = raiz.dataset.latido === "1" ? "0" : "1";
      }
    }
    if (E.vista !== "inicio") return;                  // el reactor y la onda solo existen en Inicio

    for (const anillo of anillos) {
      const velocidad = anillo.velocidad();
      if (!velocidad) continue;
      anillo.angulo = (anillo.angulo + velocidad * transcurrido) % 360;
      anillo.nodo.style.transform = `rotate(${anillo.angulo.toFixed(1)}deg)`;
    }

    if (lienzo.dataset.ancho !== String(lienzo.clientWidth)) {
      if (!ajustar()) return;
      lienzo.dataset.ancho = String(lienzo.clientWidth);
    }
    const ancho = lienzo.clientWidth, alto = lienzo.clientHeight, centro = alto / 2;
    const segundos = ahora / 1000;

    // Cuánto se mueve la onda según lo que esté haciendo JARVIS.
    let objetivo = .34 + .06 * Math.sin(segundos * 1.3);          // en reposo: una onda suave, viva
    if (E.estado === "escuchando") objetivo = Math.max(.4, Math.min(1, E.nivel * 1.4));
    else if (E.estado === "hablando") objetivo = .6 + .36 * Math.abs(Math.sin(segundos * 6.3) * Math.sin(segundos * 2.1));
    else if (E.estado === "procesando") objetivo = .44 + .1 * Math.sin(segundos * 5);
    else if (E.modo === "voz") objetivo = Math.max(objetivo, Math.min(1, E.nivel * 1.2));
    nivel += (objetivo - nivel) * (enPausa ? 1 : .6);

    // El núcleo y el halo del reactor laten con la voz; la propiedad solo se toca cuando cambia.
    const pulso = (E.estado === "hablando" || E.estado === "escuchando") ? nivel.toFixed(2) : "0";
    if (pulso !== ultimoPulso) { orbe.style.setProperty("--pulso", pulso); ultimoPulso = pulso; }

    contexto.clearRect(0, 0, ancho, alto);
    if (!linea || anchoLinea !== ancho) {
      linea = contexto.createLinearGradient(0, 0, ancho, 0);
      linea.addColorStop(0, "rgba(180,75,255,0)");
      linea.addColorStop(.5, "rgba(231,204,255,.8)");
      linea.addColorStop(1, "rgba(180,75,255,0)");
      anchoLinea = ancho;
    }
    contexto.fillStyle = linea;
    contexto.fillRect(0, centro - .5, ancho, 1);

    const zona = Math.min(ancho * .62, 340), paso = zona / BARRAS, inicio = (ancho - zona) / 2;
    const grosor = Math.max(1.4, paso * .5);
    for (let i = 0; i < BARRAS; i++) {
      const posicion = i / (BARRAS - 1);
      const campana = Math.pow(Math.sin(Math.PI * posicion), 1.6);
      const ruido = .55 + .45 * Math.sin(segundos * (5.5 + (i % 7)) + i * 1.7) * Math.cos(segundos * 3.1 + i * .6);
      alturas[i] += (Math.max(1.2, campana * ruido * nivel * alto * .95) - alturas[i]) * (enPausa ? 1 : .7);
    }
    // Dos pasadas baratas en lugar de sombras: primero el resplandor, luego la barra.
    contexto.fillStyle = "rgba(180,75,255,.3)";
    for (let i = 0; i < BARRAS; i++) {
      const x = inicio + i * paso + paso * .22;
      contexto.fillRect(x - 1.5, centro - alturas[i] / 2 - 2, grosor + 3, alturas[i] + 4);
    }
    contexto.fillStyle = "rgba(236,214,255,.96)";
    for (let i = 0; i < BARRAS; i++) {
      const x = inicio + i * paso + paso * .22;
      contexto.fillRect(x, centro - alturas[i] / 2, grosor, alturas[i]);
    }
  }
  (function bucle() {
    fotograma();
    setTimeout(bucle, E.estado === "reposo" ? INTERVALO_REPOSO : INTERVALO);
  })();
}

function subtitular(mensaje) {
  const nodo = $("#subtitulo");
  nodo.textContent = mensaje.rol === "usuario" ? `«${mensaje.texto}»` : String(mensaje.texto ?? "").replaceAll("**", "");
  nodo.classList.toggle("oido", mensaje.rol === "usuario");
}

// --------------------------------------------------------------------------
// Conversación
// --------------------------------------------------------------------------
function pintarConversacion(mensajes) {
  const contenedor = $("#mensajes");
  contenedor.replaceChildren();
  E.mensajes.clear();
  if (!mensajes || !mensajes.length) {
    contenedor.append(el("div", { class: "vacio", id: "chat-vacio", text: t("chat.vacio") }));
    $("#subtitulo").textContent = "";
  } else {
    for (const mensaje of mensajes) pintarMensaje(mensaje, false);
    const primero = mensajes.find((m) => m.rol === "usuario");
    if (primero && !E.tituloConversacion) E.tituloConversacion = primero.texto;
  }
  pintarTitulo();
  alFinal(false);
}

function pintarTitulo() {
  $("#chat-titulo").textContent = E.tituloConversacion || t("chat.nueva");
}

function alFinal(suave = true) {
  const contenedor = $("#mensajes");
  contenedor.scrollTo({ top: contenedor.scrollHeight, behavior: suave ? "smooth" : "auto" });
}

function mostrarEscribiendo() {
  if ($("#escribiendo") || [...E.mensajes.values()].some((nodo) => nodo.classList.contains("transmitiendo"))) return;
  $("#chat-vacio")?.remove();
  $("#mensajes").append(el("div", { class: "mensaje asistente", id: "escribiendo" },
    el("span", { class: "avatar" }),
    el("div", { class: "mensaje-cuerpo" }, el("div", { class: "burbuja" }, el("span", { class: "escribiendo" }, el("i"), el("i"), el("i"))))));
  alFinal();
}
const quitarEscribiendo = () => $("#escribiendo")?.remove();

function nodoMensaje(id, rol) {
  let nodo = E.mensajes.get(id);
  if (!nodo) {
    $("#chat-vacio")?.remove();
    quitarEscribiendo();
    nodo = el("div", { class: `mensaje ${rol}` });
    $("#mensajes").append(nodo);
    E.mensajes.set(id, nodo);
  }
  return nodo;
}

function pintarFragmento(id, trozo) {
  const nodo = nodoMensaje(id, "asistente");
  if (!nodo.classList.contains("transmitiendo")) {
    nodo.classList.add("transmitiendo");
    nodo.replaceChildren(el("span", { class: "avatar" }), el("div", { class: "mensaje-cuerpo" }, el("div", { class: "burbuja" })));
  }
  $(".burbuja", nodo).append(trozo);
  alFinal();
}

function pintarMensaje(mensaje, desplazar = true) {
  const esUsuario = mensaje.rol === "usuario";
  const nodo = nodoMensaje(mensaje.id, esUsuario ? "usuario" : "asistente");
  // El tipo lleva prefijo para no chocar con clases de la maquetación (.chat, .tiempo…).
  nodo.className = `mensaje ${esUsuario ? "usuario" : `asistente tipo-${mensaje.tipo || "chat"}`}`;
  const cuerpo = el("div", { class: "mensaje-cuerpo" },
    el("div", { class: "burbuja" }, esUsuario ? mensaje.texto : conFormato(mensaje.texto)));
  if (!esUsuario) {
    const adjunto = crearAdjunto(mensaje);
    if (adjunto) cuerpo.append(adjunto);
    if (mensaje.sugerencias?.length) {
      cuerpo.append(el("div", { class: "chips" }, mensaje.sugerencias.map((s) =>
        el("button", { class: "chip", text: s.etiqueta, onclick: () => enviar(s.comando) }))));
    }
  }
  const meta = el("div", { class: "meta" });
  if (esUsuario && mensaje.origen === "voz") meta.append(icono("microfono"), t("chat.por_voz"), " · ");
  if (mensaje.fecha) meta.append(horaCorta(mensaje.fecha));
  cuerpo.append(meta);
  nodo.replaceChildren(esUsuario ? "" : el("span", { class: "avatar" }), cuerpo);
  if (esUsuario && !E.tituloConversacion) { E.tituloConversacion = mensaje.texto; pintarTitulo(); }
  if (desplazar) alFinal();
}

// Las respuestas del modelo pueden traer **negritas**: se muestran como tales, sin interpretar HTML.
function conFormato(texto) {
  return String(texto ?? "").split(/\*\*([^*\n]+?)\*\*/g).map((parte, i) => (i % 2 ? el("b", { text: parte }) : parte));
}

function crearAdjunto(mensaje) {
  const datos = mensaje.datos || {};
  if (mensaje.tipo === "tiempo" && datos.ciudad) {
    const grados = datos.temperatura != null ? `${datos.temperatura}°C` : `${datos.minima}° – ${datos.maxima}°`;
    const frio = ["luna", "nubes", "lluvia", "nieve", "tormenta", "niebla"].includes(datos.icono);
    return el("div", { class: `adjunto tiempo ${frio ? "frio" : ""}` },
      icono(datos.icono || "sol"),
      el("div", {}, el("div", { class: "grados", text: grados })),
      el("div", {},
        el("div", { class: "ciudad", text: datos.ciudad + (datos.dia === "manana" ? " · mañana" : "") }),
        el("div", { class: "detalle", text: `${datos.descripcion.charAt(0).toUpperCase()}${datos.descripcion.slice(1)}` }),
        el("div", { class: "detalle", text: `Máx. ${datos.maxima}° · Mín. ${datos.minima}°` + (datos.prob_lluvia != null ? ` · Lluvia ${datos.prob_lluvia} %` : "") })));
  }
  if (mensaje.tipo === "musica" && (datos.sesion || datos.titulo || datos.consulta)) return tarjetaMusica(datos);
  if (mensaje.tipo === "equipo" && datos.equipo) return tarjetaEquipo(datos.equipo);
  if (datos.archivos?.length) {
    return el("ul", { class: "adjunto lista-adjunta" }, datos.archivos.slice(0, 8).map((a, i) =>
      el("li", {}, el("button", { class: "fila", title: a.ruta, onclick: () => enviar(`abre el resultado ${i + 1}`) },
        icono("tarea"),
        el("span", { class: "titulo" }, el("b", { text: a.nombre }),
          el("small", { text: `${a.carpeta}` + (a.fecha ? ` · ${fechaCorta(a.fecha)}` : "") })),
        icono("externo")))));
  }
  if (datos.recordatorios?.length) {
    return el("ul", { class: "adjunto lista-adjunta" }, datos.recordatorios.map((r) =>
      el("li", {}, icono(ICONO_RECORDATORIO[r.tipo] || "reloj"),
        el("span", { class: "titulo" }, el("b", { text: r.texto || t(`recordatorio.${r.tipo}`) }),
          el("small", { text: `${t(`recordatorio.${r.tipo}`)} · ${r.cuando}` + (r.origen === "calendario" ? ` · ${t("recordatorio.calendario")}` : "") })))));
  }
  if (datos.resultados?.length) {
    return el("ul", { class: "adjunto lista-adjunta" }, datos.resultados.slice(0, 5).map((r) =>
      el("li", {}, el("button", { class: "fila", title: r.url, onclick: () => abrirEnlace(r.url) },
        icono(datos.noticias ? "internet" : "buscar"),
        el("span", { class: "titulo" }, el("b", { text: r.titulo }), el("small", { text: r.fuente || "" })),
        icono("externo")))));
  }
  if (datos.recuerdos?.length) {
    return el("ul", { class: "adjunto lista-adjunta" }, datos.recuerdos.map((r) =>
      el("li", {}, icono(ICONO_CATEGORIA[r.categoria] || "memoria"),
        el("span", { class: "titulo" }, el("b", { text: r.texto }), el("small", { text: `${t("categoria." + r.categoria)} · ${fechaCorta(r.fecha)}` })))));
  }
  return null;
}

function tarjetaEquipo(e) {
  const fila = (nombre, valor, porcentaje) => el("li", { class: "medidor" },
    el("span", { class: "titulo" }, el("b", { text: nombre }), el("small", { text: valor })),
    porcentaje == null ? "" : el("span", { class: "barra" }, el("i", { style: `width:${Math.max(0, Math.min(100, porcentaje))}%` })));
  const filas = [fila("Procesador", `${e.cpu} %`, e.cpu), fila("Memoria", `${e.ram} % de ${String(e.ram_total_gb).replace(".", ",")} GB`, e.ram),
    fila(`Disco ${e.disco}`, `${e.disco_libre_gb} GB libres`, e.disco_uso)];
  if (e.gpu) filas.push(fila("Gráfica", `${e.gpu.uso} % · ${e.gpu.temperatura} °C`, e.gpu.uso));
  if (e.bateria) filas.push(fila("Batería", `${e.bateria.porcentaje} %${e.bateria.enchufado ? " · enchufado" : ""}`, e.bateria.porcentaje));
  return el("ul", { class: "adjunto lista-adjunta equipo" }, filas);
}

function tarjetaMusica(datos) {
  // Lo que suena de verdad (controles de Windows) o lo que se acaba de poner.
  const sesion = datos.sesion;
  const servicio = datos.servicio === "spotify" ? "Spotify" : "YouTube";
  const titulo = sesion ? (sesion.titulo || sesion.app) : (datos.titulo || datos.consulta);
  const detalle = sesion ? [sesion.artista, sesion.app] : [datos.canal, servicio];
  const sonando = sesion ? sesion.estado === "sonando" : !!datos.confirmado;
  const estado = sesion ? t(`musica.estado.${sesion.estado}`)
    : datos.confirmado ? t("musica.estado.sonando") : t(datos.servicio === "spotify" ? "musica.elige" : "musica.abierta");
  const tarjeta = el("div", { class: `adjunto musica ${sonando ? "sonando" : ""}` },
    el("span", { class: "disco" }, icono("musica")),
    el("div", { class: "pista" }, el("b", { text: titulo, title: titulo }), el("small", { text: detalle.filter(Boolean).join(" · ") }),
      el("span", { class: "estado-musica", text: estado })));
  if (!sesion && datos.url) {
    tarjeta.append(el("button", { class: "boton-icono pequeno", title: t("musica.abrir"), "aria-label": t("musica.abrir"),
      onclick: () => abrirEnlace(datos.url) }, icono("externo")));
  }
  return tarjeta;
}

async function abrirEnlace(url) {
  try { await api("POST", "/api/abrir", { url }); } catch (error) { aviso(error.message, "error"); }
}

async function enviar(texto) {
  texto = (texto || "").trim();
  if (!texto) return;
  if (E.modo === "texto" && E.vista !== "chat") irA("chat");
  try { await api("POST", "/api/mensaje", { texto }); } catch (error) { aviso(error.message, "error"); }
}

async function detener() {
  try { await api("POST", "/api/detener"); } catch (error) { aviso(error.message, "error"); }
}

async function hablar() {
  if (E.estado === "procesando" || E.estado === "hablando") return detener();
  try {
    const datos = await api("POST", "/api/microfono");
    if (!datos.ok) aviso(t("salud.voz.sin_microfono"), "error");
  } catch (error) { aviso(error.message, "error"); }
}

async function cambiarModo(modo) {
  try { await api("POST", "/api/ajustes", { modo }); } catch (error) { aviso(error.message, "error"); }
}

// --------------------------------------------------------------------------
// Peticiones pendientes: confirmación de acciones delicadas y sugerencias de recuerdo
// --------------------------------------------------------------------------
let relojConfirmacion = null;

function mostrarPendiente(pendiente, tipoEvento) {
  const confirmacion = $("#confirmacion"), sugerencia = $("#sugerencia");
  // Mientras haya un aviso flotante, la conversación deja hueco para que no tape el último mensaje.
  const reservarHueco = () => {
    const visible = !confirmacion.classList.contains("oculto") || !sugerencia.classList.contains("oculto");
    $("#mensajes").classList.toggle("con-aviso", visible);
    if (visible) alFinal();
  };
  if (!pendiente) {
    if (!tipoEvento || tipoEvento === "accion") { confirmacion.classList.add("oculto"); clearInterval(relojConfirmacion); }
    if (!tipoEvento || tipoEvento === "recuerdo") sugerencia.classList.add("oculto");
    reservarHueco();
    return;
  }
  if (pendiente.tipo === "recuerdo") {
    $("#sugerencia-texto").textContent = `«${pendiente.texto}»`;
    sugerencia.classList.remove("oculto");
    reservarHueco();
    return;
  }
  $("#confirmacion-descripcion").textContent = pendiente.descripcion;
  const campo = $("#confirmacion-palabra");
  campo.value = "";
  campo.classList.toggle("oculto", pendiente.modo !== "palabra");
  confirmacion.classList.remove("oculto");
  reservarHueco();
  if (pendiente.modo === "palabra") campo.focus();

  const barra = $("#confirmacion-tiempo");
  const total = Math.max(1, pendiente.expira - Date.now() / 1000);
  clearInterval(relojConfirmacion);
  relojConfirmacion = setInterval(() => {
    const queda = pendiente.expira - Date.now() / 1000;
    barra.style.transform = `scaleX(${Math.max(0, queda / total)})`;
    if (queda <= 0) { clearInterval(relojConfirmacion); confirmacion.classList.add("oculto"); reservarHueco(); }
  }, 100);
}

async function responderConfirmacion(aceptar) {
  const palabra = $("#confirmacion-palabra").value;
  if (aceptar && !$("#confirmacion-palabra").classList.contains("oculto") && !palabra) { $("#confirmacion-palabra").focus(); return; }
  $("#confirmacion-palabra").value = "";
  try { await api("POST", "/api/confirmar", { aceptar, palabra }); } catch (error) { aviso(error.message, "error"); }
}

async function responderSugerencia(aceptar) {
  $("#sugerencia").classList.add("oculto");
  try { await api("POST", "/api/sugerencia", { aceptar }); } catch (error) { aviso(error.message, "error"); }
}

// --------------------------------------------------------------------------
// Últimas acciones
// --------------------------------------------------------------------------
function nodoAccion(accion) {
  return el("li", {},
    el("span", { class: "accion-icono" }, icono(ICONO_HERRAMIENTA[accion.herramienta] || "chispa")),
    el("span", { class: "accion-texto" },
      el("b", { text: accion.descripcion || `${accion.action} ${accion.target || ""}`, title: accion.descripcion || "" }),
      el("small", { text: haceCuanto(accion.timestamp), "data-fecha": accion.timestamp })));
}

function pintarAcciones(acciones, destino = "#acciones") {
  const lista = $(destino);
  lista.replaceChildren();
  if (!acciones || !acciones.length) lista.append(el("li", { class: "vacio", text: t("acciones.vacio") }));
  else for (const accion of acciones) lista.append(nodoAccion(accion));
}

function anadirAccion(accion) {
  for (const selector of ["#acciones", "#acciones-completas"]) {
    const lista = $(selector);
    $(".vacio", lista)?.remove();
    lista.prepend(nodoAccion(accion));
    while (lista.children.length > (selector === "#acciones" ? 8 : 30)) lista.lastChild.remove();
  }
}

// --------------------------------------------------------------------------
// Historial de conversaciones
// --------------------------------------------------------------------------
async function cargarHistorial() {
  const buscar = $("#historial-buscar").value.trim();
  let datos;
  try { datos = await api("GET", `/api/conversaciones${buscar ? `?q=${encodeURIComponent(buscar)}` : ""}`); } catch { return; }
  const lista = $("#historial-lista");
  lista.replaceChildren();
  if (!datos.conversaciones.length) {
    lista.append(el("li", { class: "vacio", text: t(buscar ? "historial.sin_resultados" : "historial.vacio") }));
    return;
  }
  for (const conversacion of datos.conversaciones) {
    if (conversacion.id === E.conversacion) { E.tituloConversacion = conversacion.titulo; pintarTitulo(); }
    lista.append(el("li", {},
      el("button", { class: `conversacion ${conversacion.id === E.conversacion ? "activa" : ""}`, title: conversacion.resumen || conversacion.titulo,
        onclick: () => abrirConversacion(conversacion) },
        el("b", { text: conversacion.titulo }),
        el("small", { text: `${haceCuanto(conversacion.fecha_fin)} · ${t("historial.mensajes", { n: conversacion.n_mensajes })}` })),
      el("button", { class: "borrar", title: t("historial.borrar"), "aria-label": t("historial.borrar"),
        onclick: () => confirmarDelicado({ titulo: t("historial.borrar"), texto: t("historial.borrar.texto", { titulo: conversacion.titulo }) },
          (palabra) => api("DELETE", `/api/conversaciones/${conversacion.id}`, { palabra })).then(cargarHistorial) },
        icono("papelera"))));
  }
}

async function abrirConversacion(conversacion) {
  try {
    const datos = await api("POST", `/api/conversaciones/${conversacion.id}/abrir`);
    E.conversacion = conversacion.id;
    E.tituloConversacion = conversacion.titulo;
    pintarConversacion(datos.mensajes);
    cargarHistorial();
  } catch (error) { aviso(error.message, "error"); }
}

async function conversacionNueva() {
  try { await api("POST", "/api/conversaciones"); $('#vista-chat .compositor input').focus(); } catch (error) { aviso(error.message, "error"); }
}

// --------------------------------------------------------------------------
// Ventana modal y confirmaciones de la interfaz
// --------------------------------------------------------------------------
function abrirModal(contenido) {
  $("#modal").replaceChildren(...contenido);
  $("#capa-modal").classList.remove("oculto");
  ($("#modal input, #modal textarea") || $("#modal button"))?.focus();
}
const cerrarModal = () => $("#capa-modal").classList.add("oculto");

/* Diálogo para operaciones delicadas hechas desde la interfaz (borrar).
   Si hay palabra de seguridad se pide aquí, y el servidor la comprueba. */
function confirmarDelicado({ titulo, texto, boton = t("comun.borrar") }, accion) {
  return new Promise((resolver) => {
    const pidePalabra = E.seguridad.modo === "palabra";
    const campo = el("input", { type: "password", class: "campo", placeholder: t("comun.palabra"), autocomplete: "off" });
    const error = el("div", { class: "error" });
    const aceptar = async () => {
      try {
        await accion(campo.value);
        cerrarModal();
        resolver(true);
      } catch (fallo) {
        error.textContent = fallo.message;
        campo.value = "";
        campo.focus();
      }
    };
    campo.addEventListener("keydown", (evento) => { if (evento.key === "Enter") aceptar(); });
    abrirModal([
      el("h2", {}, icono("alerta"), titulo),
      el("p", { text: texto }),
      pidePalabra ? el("label", {}, t("comun.palabra"), campo) : null,
      error,
      el("div", { class: "botones" },
        el("button", { class: "boton fantasma", text: t("comun.cancelar"), onclick: () => { cerrarModal(); resolver(false); } }),
        el("button", { class: "boton peligro", text: boton, onclick: aceptar })),
    ]);
  });
}

function opcionesSeguridad(inicial) {
  let modo = inicial;
  const campo = el("input", { type: "password", class: "campo", placeholder: t("seguridad.nueva"), autocomplete: "new-password" });
  const envoltorio = el("label", { class: modo === "palabra" ? "" : "oculto" }, t("seguridad.nueva"), campo);
  const botones = ["confirmar", "palabra"].map((valor) => el("button", {
    type: "button", class: `opcion ${valor === modo ? "activa" : ""}`,
    onclick: () => {
      modo = valor;
      botones.forEach((b, i) => b.classList.toggle("activa", ["confirmar", "palabra"][i] === modo));
      envoltorio.classList.toggle("oculto", modo !== "palabra");
      if (modo === "palabra") campo.focus();
    } },
    el("b", { text: t(`seguridad.${valor}`) }), el("small", { text: t(`seguridad.${valor}.ayuda`) })));
  return { nodos: [el("div", { class: "opciones" }, botones), envoltorio], valor: () => ({ modo, palabra: campo.value }) };
}

function mostrarBienvenida() {
  const nombre = el("input", { type: "text", class: "campo", value: E.usuario, maxlength: 40 });
  const seguridad = opcionesSeguridad("confirmar");
  const error = el("div", { class: "error" });
  const empezar = async () => {
    try {
      const elegido = nombre.value.trim();
      if (elegido) await api("POST", "/api/ajustes", { "usuario.nombre": elegido });
      const respuesta = await api("POST", "/api/seguridad", seguridad.valor());
      E.seguridad = respuesta.seguridad;
      cerrarModal();
    } catch (fallo) { error.textContent = fallo.datos?.mensaje || fallo.message; }
  };
  abrirModal([
    el("h2", {}, icono("chispa"), t("bienvenida.titulo")),
    el("p", { text: t("bienvenida.texto") }),
    el("label", {}, t("bienvenida.nombre"), nombre),
    el("p", { text: t("bienvenida.seguridad") }),
    ...seguridad.nodos,
    error,
    el("div", { class: "botones" }, el("button", { class: "boton", text: t("bienvenida.empezar"), onclick: empezar })),
  ]);
}

// --------------------------------------------------------------------------
// Memoria
// --------------------------------------------------------------------------
async function cargarRecuerdos() {
  const buscar = $("#recuerdos-buscar").value.trim();
  const parametros = new URLSearchParams();
  if (E.categoria) parametros.set("categoria", E.categoria);
  if (buscar) parametros.set("q", buscar);
  let datos;
  try { datos = await api("GET", `/api/recuerdos?${parametros}`); } catch (error) { aviso(error.message, "error"); return; }

  $("#recuerdos-total").textContent = `(${datos.total})`;
  pintarTelemetria(datos.total);
  const pestanas = [["", t("categoria.todos"), datos.total], ...["personales", "preferencias", "información", "tareas"]
    .map((categoria) => [categoria, t(`categoria.${categoria}`), datos.conteo[categoria] || 0])];
  $("#recuerdos-pestanas").replaceChildren(...pestanas.map(([categoria, nombre, cantidad]) =>
    el("button", { class: `pestana ${categoria === E.categoria ? "activa" : ""}`, role: "tab",
      onclick: () => { E.categoria = categoria; cargarRecuerdos(); } }, nombre, el("small", { text: cantidad }))));

  const lista = $("#recuerdos-lista");
  lista.replaceChildren();
  if (!datos.recuerdos.length) {
    lista.append(el("li", { class: "vacio", text: t(buscar || E.categoria ? "recuerdos.sin_resultados" : "recuerdos.vacio") }));
    return;
  }
  for (const recuerdo of datos.recuerdos) {
    const detalle = `${t("categoria." + recuerdo.categoria)} · ${fechaCorta(recuerdo.fecha)}` + (ORIGEN_RECUERDO[recuerdo.origen] ? ` · ${t(ORIGEN_RECUERDO[recuerdo.origen])}` : "");
    lista.append(el("li", { class: "recuerdo" },
      el("span", { class: "accion-icono" }, icono(ICONO_CATEGORIA[recuerdo.categoria] || "memoria")),
      el("span", { class: "recuerdo-texto" }, el("b", { text: recuerdo.texto }), el("small", { text: detalle })),
      el("span", { class: "botones" },
        el("button", { class: "boton-icono pequeno", title: t("recuerdos.editar"), "aria-label": t("recuerdos.editar"), onclick: () => editarRecuerdo(recuerdo) }, icono("editar")),
        el("button", { class: "boton-icono pequeno borrar", title: t("recuerdos.borrar"), "aria-label": t("recuerdos.borrar"),
          onclick: () => confirmarDelicado({ titulo: t("recuerdos.borrar"), texto: t("recuerdos.borrar.texto", { texto: recuerdo.texto }) },
            (palabra) => api("DELETE", `/api/recuerdos/${recuerdo.id}`, { palabra })).then((hecho) => hecho && cargarRecuerdos()) },
          icono("papelera")))));
  }
}

function editarRecuerdo(recuerdo) {
  const nuevo = !recuerdo;
  const texto = el("textarea", { class: "campo", maxlength: 500, placeholder: t("recuerdos.ejemplo") });
  texto.value = recuerdo?.texto || "";
  const categoria = el("select", { class: "campo" },
    el("option", { value: "", text: t("categoria.auto") }),
    ["personales", "preferencias", "información", "tareas"].map((c) => el("option", { value: c, text: t(`categoria.${c}`) })));
  categoria.value = recuerdo?.categoria || "";
  const error = el("div", { class: "error" });
  const guardar = async () => {
    const cuerpo = { texto: texto.value.trim(), categoria: categoria.value || null };
    if (!cuerpo.texto) { texto.focus(); return; }
    try {
      if (nuevo) await api("POST", "/api/recuerdos", cuerpo);
      else await api("PUT", `/api/recuerdos/${recuerdo.id}`, cuerpo);
      cerrarModal();
      cargarRecuerdos();
    } catch (fallo) { error.textContent = fallo.datos?.mensaje || fallo.message; }
  };
  abrirModal([
    el("h2", {}, icono("memoria"), t(nuevo ? "recuerdos.nuevo" : "recuerdos.editar")),
    el("label", {}, t("recuerdos.texto"), texto),
    el("label", {}, t("recuerdos.categoria"), categoria),
    error,
    el("div", { class: "botones" },
      el("button", { class: "boton fantasma", text: t("comun.cancelar"), onclick: cerrarModal }),
      el("button", { class: "boton", text: t("comun.guardar"), onclick: guardar })),
  ]);
}

// --------------------------------------------------------------------------
// Herramientas
// --------------------------------------------------------------------------
function pintarHerramientas() {
  const lista = $("#herramientas-lista");
  const abiertas = new Set($$(".herramienta.abierta", lista).map((n) => n.dataset.id));
  lista.replaceChildren(...E.herramientas.map((herramienta) => {
    const interruptor = el("button", { class: "interruptor", role: "switch", "aria-checked": String(herramienta.activa),
      "aria-label": `Activar ${herramienta.nombre}`,
      onclick: async () => {
        try { await api("POST", "/api/ajustes", { herramientas: { [herramienta.id]: !herramienta.activa } }); }
        catch (error) { aviso(error.message, "error"); }
      } });
    const nodo = el("li", { class: `herramienta ${herramienta.activa ? "" : "desactivada"} ${abiertas.has(herramienta.id) ? "abierta" : ""}`, "data-id": herramienta.id },
      el("div", { class: "herramienta-fila" },
        el("span", { class: "accion-icono" }, icono(ICONO_HERRAMIENTA[herramienta.id] || "chispa")),
        el("button", { class: "herramienta-nombre", onclick: () => nodo.classList.toggle("abierta") },
          el("b", { text: herramienta.nombre }), el("small", { text: `Ej.: ${herramienta.ejemplos.slice(0, 2).join(", ")}…` })),
        interruptor,
        el("button", { class: "flecha", "aria-label": "Ver detalles", onclick: () => nodo.classList.toggle("abierta") }, icono("flecha"))),
      el("div", { class: "herramienta-detalle" },
        el("p", { text: herramienta.descripcion }),
        el("div", { class: "chips" }, herramienta.ejemplos.map((ejemplo) =>
          el("button", { class: "chip", text: ejemplo, title: "Enviar esta orden a JARVIS", onclick: () => { irA("chat"); enviar(ejemplo); } }))),
        el("div", { class: "nota" }, icono("escudo"), herramienta.confirmacion)));
    return nodo;
  }));
}

async function cargarAcciones() {
  try { pintarAcciones((await api("GET", "/api/acciones?n=30")).acciones, "#acciones-completas"); } catch { /* sin registro todavía */ }
}

async function buscarEnInternet(consulta) {
  consulta = (consulta || "").trim();
  if (!consulta) return;
  $("#busqueda-entrada").value = consulta;
  const lista = $("#busqueda-resultados");
  lista.replaceChildren(el("li", { class: "vacio", text: t("busqueda.buscando") }));
  try {
    const datos = await api("POST", "/api/buscar", { consulta, tipo: /noticias/i.test(consulta) ? "noticias" : "web" });
    lista.replaceChildren();
    if (!datos.resultados.length) lista.append(el("li", { class: "vacio", text: t("busqueda.vacio", { consulta }) }));
    for (const resultado of datos.resultados) {
      lista.append(el("li", {}, el("button", { class: "resultado", title: resultado.url, onclick: () => abrirEnlace(resultado.url) },
        icono("internet"),
        el("span", {}, el("b", { text: resultado.titulo }), el("small", { text: resultado.fuente }), el("p", { text: resultado.resumen })))));
    }
  } catch (error) {
    lista.replaceChildren(el("li", { class: "vacio", text: error.message }));
  }
}

// --------------------------------------------------------------------------
// Ajustes
// --------------------------------------------------------------------------
const valorDe = (clave) => clave.split(".").reduce((nodo, parte) => nodo?.[parte], E.ajustes.valores);

async function guardarAjuste(clave, valor) {
  try {
    const datos = await api("POST", "/api/ajustes", { [clave]: valor });
    if (datos.rechazadas[clave]) aviso(datos.rechazadas[clave], "error");
    E.ajustes = datos.ajustes;
  } catch (error) { aviso(error.message, "error"); }
}

function fila(nombre, ayuda, control, columna = false) {
  return el("div", { class: `ajuste ${columna ? "columna" : ""}` },
    el("div", {}, el("span", { class: "nombre", text: nombre }), ayuda ? el("small", { text: ayuda }) : null), control);
}
function controlTexto(clave, marcador = "") {
  const campo = el("input", { type: "text", class: "campo", placeholder: marcador, maxlength: 80 });
  campo.value = valorDe(clave) ?? "";
  campo.addEventListener("change", () => guardarAjuste(clave, campo.value.trim()));
  return campo;
}
function controlLista(clave, opciones) {
  const lista = el("select", { class: "campo" }, opciones.map(([valor, nombre, desactivada]) =>
    el("option", { value: valor, text: nombre, disabled: desactivada })));
  lista.value = valorDe(clave);
  lista.addEventListener("change", () => guardarAjuste(clave, lista.value));
  return lista;
}
function controlInterruptor(clave) {
  const boton = el("button", { class: "interruptor", role: "switch", "aria-checked": String(!!valorDe(clave)) });
  boton.addEventListener("click", () => {
    const nuevo = boton.getAttribute("aria-checked") !== "true";
    boton.setAttribute("aria-checked", String(nuevo));
    guardarAjuste(clave, nuevo);
  });
  return boton;
}
function controlRango(clave, minimo, maximo, paso, formato) {
  const rango = el("input", { type: "range", min: minimo, max: maximo, step: paso });
  rango.value = valorDe(clave);
  const etiqueta = el("span", { class: "valor-rango", text: formato(Number(rango.value)) });
  rango.addEventListener("input", () => { etiqueta.textContent = formato(Number(rango.value)); });
  rango.addEventListener("change", () => guardarAjuste(clave, Number(rango.value)));
  return el("span", { class: "grupo" }, rango, etiqueta);
}
function controlSegmentos(clave, opciones) {
  const actual = valorDe(clave);
  const botones = opciones.map(([valor, nombre]) => el("button", { class: valor === actual ? "activo" : "", text: nombre,
    onclick: () => { botones.forEach((b, i) => b.classList.toggle("activo", opciones[i][0] === valor)); guardarAjuste(clave, valor); } }));
  return el("div", { class: "segmentos" }, botones);
}
const tarjeta = (iconoNombre, titulo, ...filas) => el("div", { class: "tarjeta" }, el("h3", {}, icono(iconoNombre), titulo), ...filas);

function pintarAjustes() {
  if (!E.ajustes) return;
  const a = E.ajustes;
  const modelos = a.modelos.length ? a.modelos : [valorDe("ia.modelo")];
  const modeloActual = modelos.find((m) => m === valorDe("ia.modelo") || m.split(":")[0] === valorDe("ia.modelo")) || valorDe("ia.modelo");
  const listaModelos = el("select", { class: "campo" }, [...new Set([modeloActual, ...modelos])].map((m) => el("option", { value: m, text: m })));
  listaModelos.value = modeloActual;
  listaModelos.addEventListener("change", () => guardarAjuste("ia.modelo", listaModelos.value));

  const clave = el("input", { type: "password", class: "campo", autocomplete: "off", placeholder: a.clave_elevenlabs ? "Clave guardada ✓" : "Pega aquí tu clave" });
  const guardarClave = el("button", { class: "boton mini fantasma", text: t("comun.guardar"), onclick: async () => {
    try {
      const datos = await api("POST", "/api/clave", { servicio: "elevenlabs", clave: clave.value });
      clave.value = "";
      aviso(datos.configurada ? "Clave de ElevenLabs guardada en este ordenador." : "Clave de ElevenLabs eliminada.");
      E.ajustes = await api("GET", "/api/ajustes");
      pintarAjustes();
    } catch (error) { aviso(error.message, "error"); }
  } });

  const motores = a.motores_habla;
  $("#ajustes").replaceChildren(
    tarjeta("usuario", "Perfil",
      fila("Tu nombre", "Así se dirigirá JARVIS a ti.", controlTexto("usuario.nombre", "Tu nombre")),
      fila("Tu ciudad", "Para «¿qué tiempo hace?» sin tener que decirla.", controlTexto("internet.ciudad", "Por ejemplo, Valencia"))),

    tarjeta("chispa", "Asistente",
      fila("Modelo de lenguaje", "Modelos instalados en Ollama, en local. Usa uno que sepa usar herramientas (como granite4.1:8b): así busca los datos en vez de inventarlos.", listaModelos),
      fila("Sarcasmo", "Cuánto carácter le pone a las respuestas.", controlSegmentos("personalidad.sarcasmo", [[0, "Neutro"], [1, "Suave"], [2, "Sarcástico"], [3, "Afilado"]]), true),
      fila("Creatividad", "Más alto, charla más variada (por encima de 0,8 empieza a inventar). Para datos y acciones se usa como mucho 0,3.", controlRango("ia.temperatura", 0, 1.5, 0.1, (v) => v.toFixed(1))),
      fila("Idioma", "Más idiomas en una próxima versión.", controlLista("idioma", [["es", "Español"]])),
      fila("Música", "Dónde pone las canciones que le pides. Con YouTube empieza a sonar sola; Spotify no deja elegir la canción desde fuera, así que abre la búsqueda.",
        controlLista("musica.servicio", [["youtube", "YouTube"], ["spotify", "Spotify"]])),
      fila("Asistente flotante", "El reactor en pequeño, siempre encima de las demás ventanas: púlsalo (o Ctrl+Alt+J) para hablar, arrástralo para moverlo y con clic derecho tienes el menú.",
        controlInterruptor("interfaz.overlay")),
      fila("Aceleración gráfica", "Anima la ventana con la tarjeta gráfica: gasta menos procesador, pero en algunos equipos la cierra de golpe (si pasa, se desactiva sola). Se aplica al reiniciar JARVIS.",
        controlInterruptor("interfaz.aceleracion_grafica"))),

    tarjeta("reloj", "Agenda",
      fila("Calendario (iCal)", "Opcional, solo lectura. En Google Calendar: Configuración → tu calendario → «Dirección secreta en formato iCal». Se guarda solo en este ordenador.",
        controlTexto("agenda.calendario", "https://… .ics")),
      fila("Avisar antes de cada evento", "Minutos de antelación para los eventos del calendario.",
        controlRango("agenda.aviso_minutos", 0, 120, 5, (v) => `${v} min`)),
      fila("Resumen al empezar el día", "La primera vez que abres JARVIS por la mañana te dice el tiempo, lo que tienes hoy y unos titulares. Con «buenos días» te lo da siempre (una vez al día).",
        controlInterruptor("resumen.automatico")),
      fila("Titulares en el resumen", "Tres titulares de las noticias del día (necesita Internet).", controlInterruptor("resumen.noticias"))),

    tarjeta("app", "Móvil",
      fila("Usar JARVIS desde el móvil", "Desde el navegador del móvil, en tu misma Wi-Fi (nunca por Internet). Se aplica al reiniciar JARVIS; la primera vez, Windows preguntará si Python puede usar la red privada: dile que sí. La voz sale por el ordenador.",
        controlInterruptor("movil.activo")),
      el("div", { class: "ajuste", id: "movil-acceso" }, el("span", { class: "texto-suave", text: "Desactivado." }))),

    tarjeta("microfono", "Escucha",
      fila("Motor de reconocimiento", "Whisper funciona en local; Google necesita Internet.", controlLista("voz.motor_escucha", [["whisper", "Whisper (local)"], ["google", "Google"]])),
      fila("Modelo de Whisper", a.whisper_descargado ? "Más grande = más preciso y más lento." : "Se descargará la primera vez que actives la voz.",
        controlLista("voz.modelo_whisper", [["tiny", "Tiny · muy rápido"], ["base", "Base · equilibrado"], ["small", "Small · preciso"], ["medium", "Medium · muy preciso"]])),
      fila("Palabra de activación", "La palabra que despierta a JARVIS.", controlTexto("voz.palabra_activacion", "jarvis")),
      fila("Detector de la palabra de activación", a.activacion_disponible
        ? "Whisper transcribe todo lo que oye para buscar «Jarvis». openWakeWord solo atiende tras «Hey Jarvis»: gasta mucho menos."
        : "Para «Hey Jarvis» con openWakeWord (más ligero) hay que instalarlo: pip install openwakeword.",
        controlLista("voz.motor_activacion", [["whisper", "Whisper · «Jarvis»"],
          ["openwakeword", a.activacion_disponible ? "openWakeWord · «Hey Jarvis»" : "openWakeWord (no instalado)", !a.activacion_disponible]])),
      fila("Ventana de fluidez", "Segundos que sigue atento sin repetir la palabra.", controlRango("voz.ventana_fluidez", 0, 30, 1, (v) => `${v} s`)),
      fila("Pausa de fin de frase", "Silencio que da por terminada una frase.", controlRango("voz.silencio_fin", 0.4, 3, 0.1, (v) => `${v.toFixed(1)} s`))),

    tarjeta("voz", "Habla",
      fila("Voz", "Femenina o masculina (Google y Windows solo tienen femenina).",
        controlSegmentos("voz.genero", [["femenino", "Femenina"], ["masculino", "Masculina"]]), true),
      fila("Motor de voz", "La neuronal de Microsoft es la más natural. Si un motor falla, JARVIS prueba el siguiente.", controlLista("voz.motor_habla", [
        ["edge", "Microsoft · neuronal (recomendada)", !motores.edge],
        ["elevenlabs", motores.elevenlabs ? "ElevenLabs" : "ElevenLabs (falta la clave)"],
        ["google", "Google · más robótica", !motores.google], ["windows", "Windows · sin conexión", !motores.windows]])),
      fila("Voz neuronal", "Solo con el motor de Microsoft. «Automática» elige según la voz femenina o masculina.", controlLista("voz.voz_edge", a.voces_edge.map((v) => [v.id, v.nombre]))),
      fila("Voz de ElevenLabs", "Identificador de voz (vacío = automática).", controlTexto("voz.voz_elevenlabs", "Automática")),
      fila("Clave de ElevenLabs", "Opcional. Se guarda solo en este ordenador.", el("span", { class: "grupo" }, clave, guardarClave), true),
      fila("Velocidad", null, controlRango("voz.velocidad", 0.6, 1.6, 0.05, (v) => `${v.toFixed(2)}×`)),
      fila("Leer en modo texto", "Leer las respuestas en voz alta aunque escribas.", controlInterruptor("voz.leer_en_modo_texto")),
      el("div", { class: "ajuste" }, el("span", { class: "texto-suave", text: "Comprueba cómo suena con los ajustes actuales." }),
        el("button", { class: "boton mini", text: "Probar voz", onclick: () => api("POST", "/api/probar_voz").catch((e) => aviso(e.message, "error")) }))),

    tarjeta("escudo", t("seguridad.titulo"),
      el("div", { class: "ajuste" },
        el("div", {}, el("span", { class: "nombre", text: t(`seguridad.modo.${E.seguridad.modo}`) }),
          el("small", { text: E.seguridad.modo === "palabra" ? "Cada acción delicada exige tu palabra." : "Cada acción delicada pide un «confirmo»." })),
        el("button", { class: "boton mini fantasma", text: "Cambiar", onclick: cambiarSeguridad })),
      el("p", { class: "texto-suave", text: "Se consideran delicadas: cerrar programas, escribir en aplicaciones, borrar recuerdos o conversaciones, apagar, reiniciar o suspender el equipo, vaciar la papelera y abrir herramientas del sistema." })),

    tarjeta("memoria", "Privacidad",
      fila("Guardar el historial", "Conserva las conversaciones para poder retomarlas.", controlInterruptor("memoria.guardar_historial")),
      fila("Aprender de lo que hablamos", "Guarda sola lo que cuentas de ti (gustos, costumbres, personas importantes). Te avisa cada vez y lo puedes revisar o borrar en Memoria. Nunca guarda contraseñas ni datos bancarios.",
        controlInterruptor("memoria.aprender_automaticamente")),
      fila("Preguntar antes de guardar", "Si no aprende sola: cuando cuentas algo personal, JARVIS pregunta si lo guarda.", controlInterruptor("memoria.sugerir_recuerdos")),
      el("div", { class: "separador" }),
      el("div", { class: "ajuste" }, el("span", { class: "texto-suave", text: "Borrar todas las conversaciones guardadas." }),
        el("button", { class: "boton mini peligro", text: "Borrar historial", onclick: () =>
          confirmarDelicado({ titulo: "Borrar el historial", texto: "Se borrarán todas las conversaciones guardadas. No se puede deshacer." },
            (palabra) => api("DELETE", "/api/conversaciones", { palabra })).then((hecho) => hecho && aviso("Historial borrado.")) })),
      el("div", { class: "ajuste" }, el("span", { class: "texto-suave", text: "Hacer que JARVIS olvide todos sus recuerdos." }),
        el("button", { class: "boton mini peligro", text: "Borrar recuerdos", onclick: () =>
          confirmarDelicado({ titulo: t("recuerdos.borrar_todos"), texto: "JARVIS olvidará todo lo que recuerda de ti. No se puede deshacer." },
            (palabra) => api("DELETE", "/api/recuerdos", { palabra })).then((hecho) => hecho && aviso("Recuerdos borrados.")) }))),
  );
  pintarAccesoMovil();
}

async function pintarAccesoMovil() {
  const caja = $("#movil-acceso");
  if (!caja) return;
  let estado;
  try { estado = await api("GET", "/api/movil"); } catch { return; }
  if (!estado.activo) {
    caja.replaceChildren(el("span", { class: "texto-suave", text: valorDe("movil.activo") ? "Reinicia JARVIS para activarlo." : "Desactivado." }));
    return;
  }
  const direccion = estado.direcciones[0] || "(sin red)";
  caja.replaceChildren(
    el("div", {}, el("span", { class: "nombre", text: `En el móvil abre ${direccion}` }),
      el("small", { text: `PIN: ${estado.pin}` + (estado.direcciones.length > 1 ? ` · también: ${estado.direcciones.slice(1).join(", ")}` : "") })),
    el("button", { class: "boton mini fantasma", text: "Cambiar PIN", onclick: async () => {
      try { await api("POST", "/api/movil/pin"); pintarAccesoMovil(); aviso("PIN cambiado: las sesiones del móvil se han cerrado."); }
      catch (error) { aviso(error.message, "error"); }
    } }));
}

function cambiarSeguridad() {
  const seguridad = opcionesSeguridad(E.seguridad.modo);
  const actual = el("input", { type: "password", class: "campo", placeholder: t("seguridad.actual"), autocomplete: "off" });
  const error = el("div", { class: "error" });
  const guardar = async () => {
    try {
      const datos = await api("POST", "/api/seguridad", { ...seguridad.valor(), palabra_actual: actual.value });
      E.seguridad = datos.seguridad;
      cerrarModal();
      aviso(datos.mensaje);
      pintarAjustes();
    } catch (fallo) { error.textContent = fallo.datos?.mensaje || fallo.message; }
  };
  abrirModal([
    el("h2", {}, icono("escudo"), t("seguridad.titulo")),
    E.seguridad.modo === "palabra" ? el("label", {}, t("seguridad.actual"), actual) : null,
    ...seguridad.nodos,
    error,
    el("div", { class: "botones" },
      el("button", { class: "boton fantasma", text: t("comun.cancelar"), onclick: cerrarModal }),
      el("button", { class: "boton", text: t("comun.guardar"), onclick: guardar })),
  ]);
}

// --------------------------------------------------------------------------
// Navegación y enlaces con la página
// --------------------------------------------------------------------------
function irA(vista) {
  E.vista = vista;
  for (const boton of $$(".nav-item")) boton.classList.toggle("activo", boton.dataset.vista === vista);
  for (const seccion of $$(".vista")) seccion.classList.toggle("activa", seccion.id === `vista-${vista}`);
  if (vista === "chat") { cargarHistorial(); alFinal(false); $("#vista-chat .compositor input").focus(); }
  if (vista === "memoria") cargarRecuerdos();
  if (vista === "herramientas") { pintarHerramientas(); cargarAcciones(); }
  if (vista === "ajustes") api("GET", "/api/ajustes").then((datos) => { E.ajustes = datos; pintarAjustes(); }).catch(() => pintarAjustes());
}

function enlazar() {
  for (const boton of $$(".nav-item")) boton.addEventListener("click", () => irA(boton.dataset.vista));
  for (const boton of $$(".modo-boton")) boton.addEventListener("click", () => cambiarModo(boton.dataset.modo));

  for (const formulario of $$("[data-compositor]")) {
    const entrada = $("input", formulario);
    formulario.addEventListener("submit", (evento) => {
      evento.preventDefault();
      if (E.estado === "procesando" || E.estado === "hablando") { if (!entrada.value.trim()) return detener(); }
      const texto = entrada.value;
      entrada.value = "";
      enviar(texto);
    });
    $('[data-accion="microfono"]', formulario).addEventListener("click", hablar);
  }
  $("#orbe").addEventListener("click", hablar);

  $("#confirmacion-si").addEventListener("click", () => responderConfirmacion(true));
  $("#confirmacion-no").addEventListener("click", () => responderConfirmacion(false));
  $("#confirmacion-palabra").addEventListener("keydown", (evento) => { if (evento.key === "Enter") responderConfirmacion(true); });
  $("#sugerencia-si").addEventListener("click", () => responderSugerencia(true));
  $("#sugerencia-no").addEventListener("click", () => responderSugerencia(false));

  $("#conversacion-nueva").addEventListener("click", conversacionNueva);
  $("#historial-alternar").addEventListener("click", () => $(".chat").classList.toggle("sin-historial"));
  $("#historial-buscar").addEventListener("input", rebote(cargarHistorial));

  $("#recuerdo-nuevo").addEventListener("click", () => editarRecuerdo(null));
  $("#recuerdos-buscar").addEventListener("input", rebote(cargarRecuerdos));

  $("#busqueda-form").addEventListener("submit", (evento) => { evento.preventDefault(); buscarEnInternet($("#busqueda-entrada").value); });
  $("#busqueda-sugerencias").replaceChildren(...["Noticias sobre tecnología", "Recetas fáciles para cenar", "Rutas de senderismo cerca de Valencia"]
    .map((texto) => el("button", { class: "chip", text: texto, onclick: () => buscarEnInternet(texto) })));

  $("#capa-modal").addEventListener("mousedown", (evento) => {
    // La bienvenida no se cierra pulsando fuera: hay que elegir la seguridad.
    if (evento.target.id === "capa-modal" && E.seguridad.configurada) cerrarModal();
  });
  document.addEventListener("keydown", (evento) => {
    if (evento.key === "Escape") {
      if (!$("#capa-modal").classList.contains("oculto")) { if (E.seguridad.configurada) cerrarModal(); }
      else if (E.estado === "procesando" || E.estado === "hablando") detener();
    }
    if (evento.ctrlKey && /^[1-5]$/.test(evento.key)) {
      evento.preventDefault();
      irA(["inicio", "chat", "memoria", "herramientas", "ajustes"][Number(evento.key) - 1]);
    }
  });
}

document.addEventListener("DOMContentLoaded", iniciar);
})();
