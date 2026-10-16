# SubSynq

Skapa svenska AI-undertexter till filmer, serieavsnitt och klipp med ett
Docker-baserat webbgränssnitt. SubSynq läser ljudet, översätter dialogen,
granskar resultatet och sparar en SRT-fil bredvid videon.

[Kom igång](#kom-igång) · [Användning](#användning) · [Lagring](#lagring) · [Dokumentation](#dokumentation)

> **Under utveckling.** En färdig körning betyder att programmets kontroller
> har passerat; kontrollera undertexten mot ljudet innan du betraktar den som
> godkänd.

## Vad du kan göra

- Bläddra bland **Filmer**, **Serier** och **Extra**, med serieavsnitt grupperade i säsonger.
- Skanna bibliotekets video-, ljud- och undertextspår och se undertexttäckning.
- Skapa svenska AI-undertexter och välja en extra engelsk undertext i inställningarna.
- Markera flera filer i biblioteket och köa undertexter eller ta bort AI-undertexter på en gång.
- Följa jobb i kön, avbryta en körning och se historik och fel i loggen.
- **Städa undertexter:** hitta gamla eller trasiga `.srt`-filer (andra än SubSynqs egna, tomma eller med fel teckenkodning, dubbletter, undertexter vars video bytts ut) och flytta dem till en återställbar papperskorg.
- Välja hårdvara och tema i webbgränssnittet.

## Kom igång

Projektet byggs från källkod med Docker Compose, på Unraid och på andra Docker-värdar:

```bash
git clone https://github.com/Jfb3rg/subsynq.git
cd subsynq
cp .env.example .env        # ange port, användare, adress och dina mediamappar
mkdir -p data/config data/models
docker compose up -d --build
```

Öppna sedan `http://<värdens adress>:8765`. **Första starten hämtar AI-modellerna
automatiskt** (se [Modeller](#modeller-och-körsätt)); förloppet visas överst i
webbgränssnittet.

Så här ställer du in `.env` på Unraid:

| Inställning | Värde på Unraid |
| --- | --- |
| `SUBSYNQ_UID` / `SUBSYNQ_GID` | `99` / `100` (Unraids användare `nobody:users`, som oftast äger media) |
| `SUBSYNQ_MOVIES_PATH`, `SUBSYNQ_TV_PATH`, `SUBSYNQ_EXTRA_PATH` | dina mediamappar, t.ex. `/mnt/user/media/film`; mapparna måste finnas |
| `SUBSYNQ_BIND_IP` | `0.0.0.0` för att nå gränssnittet från andra enheter i hemmanätverket (standard `127.0.0.1` når bara själva värden) |

Kloning och `data/` bör ligga på en disk med gott om plats, till exempel under
`/mnt/user/appdata/subsynq`. Mediamapparna måste vara skrivbara för användaren ovan.
Webbgränssnittet saknar inloggning: exponera aldrig porten mot internet.
**Installera med Unraid-mallar i stället:** de fyra mallarna och en steg-för-steg-guide
finns i [`unraid/`](unraid/README.md). Färdiga images byggs och läggs på Docker Hub av
[`publish.yml`](.github/workflows/publish.yml) när `main` uppdateras; de är ännu inte
publicerade, och mallen för Community Apps kommer först när arkivet är publikt.

Du behöver Docker med Compose v2, befintliga mediamappar och **cirka 30 GB ledigt**
för modellerna. AI-körningen behöver också tillräckligt ledigt RAM för
modellerna **utöver andra appar på värden**. Containrarnas minnestak är inte
en garanti för att värdens minne räcker.

Installationen består av fyra tjänster:

| Tjänst | Uppgift |
| --- | --- |
| `subsynq` | Webbgränssnitt, bibliotek och jobbkön |
| `worker` | Jobbmotor, Whisper och filhantering |
| `qwen-asr` | Qwen-kontroller av osäker dialog och ordtider |
| `translate-engine` | Gemma för översättning, granskning och rättning |

## Användning

1. Öppna webbgränssnittet på installationens valda adress och port.
2. Välj hårdvara under **Inställningar** och skanna biblioteket.
3. Öppna en film, ett avsnitt eller ett klipp och välj **Skapa svensk AI-undertext**.
4. Följ arbetet under **Kö**. Vid fel visar historiken orsaken och möjlighet att köra om.
5. Öppna den skapade undertexten och kontrollera text och tider mot videon.

Manuell start är standard. Automatisk skanning och jobbstart kan väljas i
inställningarna. Befintliga SubSynq-undertexter skrivs inte över automatiskt;
borttagning via gränssnittet kräver bekräftelse.

Har filen flera ljudspår (till exempel en dubbning först och originalet efter)
lyssnar SubSynq på det engelska när det går: kommentar- och syntolkningsspår
hoppas över, en språkmärkning kontrolleras mot ljudet och spår utan märkning
avlyssnas. Finns inget engelskt spår används filens standardspår. Bredvid
**Skapa svensk AI-undertext** finns då rutan **Ljudspår** där du kan välja ett
spår själv; det valet används alltid, även vid omförsök och omkörning.

## Modeller och körsätt

Whisper large-v3 används för avlyssning. Qwen3-ASR och selektiva Whisper
large-v2-pass kontrollerar osäkra delar. Gemma 3 12B används för
källgranskning och översättning, Gemma 4 12B för slutgranskning och rättning.

**Modellerna laddas ner automatiskt** första gången workern startar (cirka 28 GB:
Whisper och Qwen från Hugging Face, Gemma via Ollama). Det kräver internet och
ledigt utrymme i `data/models`. Undertextjobb väntar tills alla modeller finns, och
förloppet visas överst på varje sida. Redan nedladdade modeller laddas inte ner igen.

Vilka modeller som används står i filen `app/models.json`, inte i koden. Vill du
byta eller uppgradera en modell lägger du en egen kopia som `/config/models.json`
(i `data/config/`) och ändrar raden: den nya modellen hämtas då automatiskt. Den
gamla ligger kvar tills du själv tar bort den under **Inställningar → Modeller**;
SubSynq tar aldrig bort något av sig själv. Kvalitetskontrollerna är avstämda mot de
modeller som listas, så kör ett prov efter ett modellbyte.

SubSynq kontrollerar också var 12:e timme om det finns en **nyare version** av de
listade modellerna. Det visas under **Inställningar → Modeller** som "ny version finns",
med en uppdateringsknapp: först när du trycker på den hämtas den nya versionen och den
gamla tas bort. Inget uppdateras av sig självt, och uppdateringen väntar tills inget
undertextjobb körs.

**Hårdvara:**

- **CPU** fungerar och är verifierad i avgränsade prov, men lång film tar lång tid.
- **NVIDIA-GPU** är målet för snabb körning, med minst 16 GB VRAM. Bilderna är
  byggda för CUDA 12.8, så värden behöver en NVIDIA-drivrutin som stöder det.
  Stödet är ännu **inte verifierat på en Unraid-värd**.
- **AMD-GPU stöds inte ännu.** Det behöver byggas separat för Linux/Unraid.
  Tidigare försök via Windows (WSL) fungerade dåligt och ingår inte.

Välj inte hårdvara enbart efter att den syns i en lista. Gemma kan kräva mycket
minne på värden, så kontrollera ledigt RAM före körning.

**Molnläget är fortfarande under utveckling.** Hela kedjan på en hyrd GPU
är inte verifierad och kort uppstart är ännu olöst. Molnjobb kan kosta
pengar och skickar ljud och dialogtext till den hyrda servern. Läs
[CLOUD-GPU.md](CLOUD-GPU.md) före användning.

## Lagring

| Sökväg i containern | Innehåll |
| --- | --- |
| `/config` | Databas, inställningar, sparad källtext och kvalitetsrapporter |
| `/models` | Modellfiler och cache |
| `/movies` | Filmbibliotek |
| `/tv` | Seriebibliotek |
| `/extra` | Klipp och övriga videofiler |

Mediamapparna kan ligga på egna diskar eller shares. Webbappen läser media och
kan, bara när du själv väljer det under **Städa undertexter**, flytta `.srt`-filer
till en papperskorg i `/config/trash` (inget raderas direkt och videofiler rörs
aldrig); containerns användare (`SUBSYNQ_UID`/`SUBSYNQ_GID`) behöver därför
skrivrätt i mediamapparna. Workern har skrivåtkomst för undertextfiler. Svensk undertext får namnet
`Film.ai.sv.srt`, och en vald engelsk extrafil får `Film.ai.en.srt`.

Modeller, media, undertexter, databas och lokala `.env`-filer ingår inte i
Git-arkivet. Webbgränssnittet saknar inloggning och är avsett för ett lokalt
nätverk. Hälsokontrollen `/health` visar serverstatus, inte undertextkvalitet.

## Dokumentation

| Dokument | Innehåll |
| --- | --- |
| [CHANGELOG.md](CHANGELOG.md) | Versionshistorik |
| [CLOUD-GPU.md](CLOUD-GPU.md) | Molnkörning, kostnader och kvarstående begränsningar |
| [Modellprov](ai/README-ASR-PROBES.md) | Separata diagnostikverktyg |

Versionsnotiser och enskilda testkörningar hör hemma i ändringshistoriken.

## Licens

SubSynqs egen kod är fri att använda, ändra och dela enligt [MIT-licensen](LICENSE).

AI-modellerna som SubSynq hämtar ingår **inte** i den licensen. De ägs av sina upphovsmakare och har egna villkor
(bland annat Whisper under MIT, Qwen3-ASR under Apache-2.0 och Gemma under Googles egna Gemma-villkor). Läs dem innan du
använder modellerna på annat sätt än för eget bruk.
