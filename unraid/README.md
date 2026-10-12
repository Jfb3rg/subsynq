# Installera SubSynq på Unraid

SubSynq består av **fyra containrar** som jobbar ihop. Du installerar dem från fyra mallar, i ordningen nedan.
Alla fyra ligger på samma Docker-nätverk, `subsynq`, och hittar varandra på namn.

| Mall | Container | Uppgift |
| --- | --- | --- |
| [`subsynq-translate-engine.xml`](subsynq-translate-engine.xml) | `subsynq-translate-engine` | Ollama med Gemma: översättning och granskning |
| [`subsynq-qwen-asr.xml`](subsynq-qwen-asr.xml) | `subsynq-qwen-asr` | Qwen3-ASR: andra avlyssning av osäker dialog |
| [`subsynq-worker.xml`](subsynq-worker.xml) | `subsynq-worker` | Jobbmotorn: Whisper, undertextfiler, hämtar AI-modellerna |
| [`subsynq.xml`](subsynq.xml) | `subsynq` | Webbgränssnittet du öppnar i webbläsaren |

## Före du börjar

- **Cirka 30 GB ledigt** på disken där `appdata` ligger (AI-modellerna är ungefär 28 GB) och internet första gången.
- **Ledigt RAM** för modellerna utöver andra appar. Översättningsmotorn är satt till högst 12 GB och Qwen till 11 GB.
- Dina mediamappar (filmer, serier, klipp). De måste vara **skrivbara**: SubSynq skriver undertextfilerna där, och fliken
  Städa undertexter kan flytta `.srt`-filer till en papperskorg. Videofiler rörs aldrig. Containrarna körs som
  Unraids vanliga användare `nobody:users` (99:100), som normalt äger Unraids delade mappar.
- Gränssnittet har **ingen inloggning**. Öppna aldrig porten mot internet.

## 1. Skapa Docker-nätverket (en gång)

Öppna Unraids terminal och kör:

```
docker network create subsynq
```

Containrar på ett eget nätverk kan nå varandra på namn. På Unraids vanliga nätverk `bridge` går det inte.

## 1b. Skapa mapparna och ge dem rätt ägare (en gång)

SubSynq körs som Unraids vanliga användare `nobody:users` (99:100). Skapar Docker modellmappen själv blir den ägd av
root, och då får workern `PermissionError: Permission denied: '/models/...'` när den ska hämta Whisper och Qwen.
Skapa mapparna först och ge dem rätt ägare (ändra sökvägarna om du valt andra i mallarna):

```
mkdir -p /mnt/user/appdata/subsynq/config /mnt/user/appdata/subsynq/models/ollama
chown 99:100 /mnt/user/appdata/subsynq /mnt/user/appdata/subsynq/config /mnt/user/appdata/subsynq/models
```

Ollama-mappen `models/ollama` ska inte ändras: Ollama körs som root och sköter den själv.

## 2. Lägg in mallarna

**Via Community Apps** (kommer senare, när SubSynq är inskickat): sök efter SubSynq och installera. Då är allt ifyllt utan
terminal.

**Direkt från GitHub** (när arkivet är publikt): kör detta i Unraids terminal. Det hämtar de fyra mallarna rakt från
GitHub och lägger dem under **User templates**:

```
for n in subsynq subsynq-worker subsynq-qwen-asr subsynq-translate-engine; do
  curl -fsSL -o /boot/config/plugins/dockerMan/templates-user/my-$n.xml \
    https://raw.githubusercontent.com/Jfb3rg/subsynq/main/unraid/$n.xml
done
```

Det är mallarna som fyller i portar, mappar och nätverk. Söker du i stället på Docker Hub i Unraid får du bara imagen utan
mall, och då är alla fält tomma. Använd alltid mallarna.

Ligger images på Docker Hub som privata måste Unraid vara inloggad: kör `docker login` i terminalen.

## 3. Installera containrarna, i den här ordningen

I fliken **Docker** väljer du **Add Container**, väljer mallen i listan, kontrollerar mapparna och trycker **Apply**.

1. `subsynq-translate-engine`
2. `subsynq-qwen-asr`
3. `subsynq-worker`
4. `subsynq` (sist)

Kontrollera i mallarna att **mapparna är samma överallt**:

- `/config` och `/models` ska peka på samma värdmapp i `subsynq` och `subsynq-worker` (och `/models` även i `subsynq-qwen-asr`).
  Förslaget är `/mnt/user/appdata/subsynq/config` och `/mnt/user/appdata/subsynq/models`.
- Ollamas mapp i `subsynq-translate-engine` ska vara undermappen `ollama` i modellmappen:
  `/mnt/user/appdata/subsynq/models/ollama`.
- Filmer, serier och klipp ska peka på dina egna mediamappar, likadant i `subsynq` och `subsynq-worker`.

## 4. Öppna SubSynq

Klicka på ikonen för `subsynq` i Docker-fliken och välj **WebUI**, eller öppna `http://<servern>:8765`.

**Första gången hämtas AI-modellerna automatiskt** (cirka 28 GB). Förloppet visas överst på varje sida, och
undertextjobb väntar tills alla modeller finns. Du behöver inte göra något, men starta inte om containrarna under tiden.
Klart när **Inställningar → Modeller** visar 6 av 6.

## Uppdateringar

Unraid visar "update ready" när nya images finns på Docker Hub. Uppdatera i den ordningen som ovan.
Nya versioner av **modellerna** (inte appen) visas under **Inställningar → Modeller** med en uppdateringsknapp.
Inget uppdateras av sig självt.

## Om något inte fungerar

| Symptom | Det första du kontrollerar |
| --- | --- |
| Containern startar inte, felet nämner nätverket | Du har inte kört `docker network create subsynq` (steg 1). |
| Gränssnittet visar att modeller saknas eller ett fel | Loggen för `subsynq-worker` (rader som börjar med `[modeller]`), internet och ledigt utrymme i modellmappen. |
| "Skapa undertext" gör inget | Banner överst: modellerna laddas fortfarande ner. Jobben startar när de är klara. |
| Modell får `PermissionError ... /models/...` | Modellmappen ägs av root. Kör `chown 99:100 /mnt/user/appdata/subsynq/models` (steg 1b) och tryck sedan på ladda ner igen under Inställningar → Modeller. |
| Workern skriver "Väntar på att webbgränssnittet skapar databasen" | Starta containern `subsynq`. Workern fortsätter av sig själv när databasen finns. |
| Undertexten sparas inte | Mediamappen är skrivskyddad eller ägs av en annan användare än 99:100. |

## GPU

Mallarna är för **CPU**. NVIDIA-stöd är ännu inte verifierat på Unraid och AMD-GPU stöds inte ännu.

## För den som publicerar imagerna

Imagerna byggs och läggs på Docker Hub av arbetsgången [`.github/workflows/publish.yml`](../.github/workflows/publish.yml)
varje gång `main` uppdateras, efter att alla tester har passerat. I GitHub (Settings → Secrets and variables → Actions)
behövs hemligheten `DOCKERHUB_TOKEN` (en åtkomstnyckel från Docker Hub, med läs- och skrivrätt). `DOCKERHUB_USERNAME`
är valfri: saknas den används `fb3rg`. Användarnamnet i mallarnas `Repository`-rader (`fb3rg/…`) måste vara samma som det som används. Saknas nyckeln skriver arbetsgången vilka hemligheter den faktiskt kan se (bara namn).
