const csvUrl = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vR1XDkBEEb3mPcd_IskPiRtNUYBWyKDynGZycSrzuqmbaXIFPfcdGCAbBjBFO6s_kdZp-8eekabv8hD/pub?output=csv';

Papa.parse(csvUrl, {
  download: true,
  header: true,
  complete: function(results) {
    const juegos = results.data;
    const container = document.getElementById("juegos-container");

    container.innerHTML = ""; // limpia el texto "Cargando..."

    juegos.forEach(juego => {
      if (!juego["Nombre"]) return; // ignora filas vacías

      const card = document.createElement("div");
      card.className = "juego-card";
      card.style.borderLeft = `10px solid ${juego["Accent Color"] || "#ccc"}`;

      card.innerHTML = `
        <div style="display: flex; align-items: center; gap: 1rem;">
          <img src="${juego["Logo"]}" alt="${juego["Nombre"]} logo" style="width: 64px; height: 64px; object-fit: contain;">
          <div>
            <h3 style="margin: 0;">${juego["Nombre"]}</h3>
            <p style="margin: 0;"><strong>Nota:</strong> ${juego["Nota"] || "—"} &nbsp; | &nbsp; <strong>Tiempo:</strong> ${juego["Tiempo"] || "—"}</p>
            <p style="margin: 0;"><strong>Fecha:</strong> ${juego["Fecha de pasado"] || "—"}</p>
          </div>
        </div>
        <p style="margin-top: 1rem;"><strong>Reseña:</strong> ${juego["Review"] || "—"}</p>
      `;

      container.appendChild(card);
    });
  }
});

