// lib/useGames.ts
import { useEffect, useState } from 'react';
import Papa from 'papaparse';

interface Game {
  nombre: string;
  tiempo: string;
  nota: string;
  completado: string;
}

export function useGames() {
  const [games, setGames] = useState<Game[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Papa.parse<Game>('https://docs.google.com/spreadsheets/d/e/2PACX-1vR1XDkBEEb3mPcd_IskPiRtNUYBWyKDynGZycSrzuqmbaXIFPfcdGCAbBjBFO6s_kdZp-8eekabv8hD/pub?gid=0&single=true&output=csv', {
      download: true,
      header: true,
      complete: (results) => {
        setGames(results.data.filter(row => row.nombre)); // Evita filas vacías
        setLoading(false);
      },
    });
  }, []);

  return { games, loading };
}
