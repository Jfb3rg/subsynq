# Automatiska tester

Mappen hör till källkoden. GitHub kör testerna före publicering för att
upptäcka återkommande fel. De kopieras inte in i webb-, worker- eller
Qwen-imagerna och behöver inte köras vid en vanlig installation.
En Git-klon innehåller testkällkod, inte sparade användarjobb.

Testerna använder Python unittest och worker-beroenden. Den kompletta
CI-miljön visas i [publish.yml](../.github/workflows/publish.yml).

Använd små avidentifierade regressionsdata och namnge filer efter felet,
inte efter film, serie, avsnitt, användare eller jobbnummer. Behåll bara
text och tider som kontrollen behöver. Lägg aldrig in riktiga mediefiler,
ljud, undertextfiler, databas, modellvikter, autentisering eller privata
rapporter. Ett grönt regressionstest är inte ett facit för en hel film.
