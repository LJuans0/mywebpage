// components/GameList.tsx
import { Card, CardContent } from "@/components/ui/card";
import { useGames } from "@/lib/useGames";
import { LoaderCircle } from "lucide-react";

export function GameList() {
  const { games, loading } = useGames();

  if (loading) return (
    <div className="flex justify-center items-center py-10">
      <LoaderCircle className="animate-spin w-6 h-6 text-muted-foreground" />
    </div>
  );

  return (
    <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
      {games.map((game, idx) => (
        <Card key={idx} className="rounded-2xl p-2">
          <CardContent className="p-4">
            <div className="font-semibold text-lg mb-1">{game.nombre}</div>
            <div className="text-sm text-muted-foreground">
              Tiempo: {game.tiempo || "—"}<br />
              Nota: {game.nota || "—"}<br />
              Completado: {game.completado || "—"}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
