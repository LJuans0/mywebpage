const csvUrl = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vR1XDkBEEb3mPcd_IskPiRtNUYBWyKDynGZycSrzuqmbaXIFPfcdGCAbBjBFO6s_kdZp-8eekabv8hD/pub?output=csv';

Papa.parse(csvUrl, {
  download: true,
  header: true,
  complete: function(results) {
    const juegos = results.data;
    const container = document.getElementById("juegos-container");

    container.innerHTML = ""; // limpia el texto "Cargando..."

    juegos.forEach(juego => {
      if (!juego.nombre) return; // ignora filas vacías

      const card = document.createElement("div");
      card.className = "juego-card";
      card.innerHTML = `
        <h3>${juego.Nombre}</h3>
        <p><strong>Tiempo:</strong> ${juego.Tiempo || "—"}</p>
        <p><strong>Nota:</strong> ${juego.Nota || "—"}</p>
        <p><strong>Review:</strong> ${juego.Review || "—"}</p>
      `;
      container.appendChild(card);
    });
  }
});
