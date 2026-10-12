# Moln-GPU för SubSynq – genomförandeunderlag

## Rättelse: oönskad spärr tas bort (kod 0.8.167, ej driftsatt)

Användaren har uttryckligen avvisat hyresspärren. Workern kräver inte längre
SUBSYNQ_CLOUD_IMAGE eller prepared=True för normal molnhyra. Den lokala
experimentimagen är frivillig och inget register behöver skapas för att
använda den vanliga hyrvägen. Tidigare instruktion om att behålla spärren
nedan är historisk och gäller inte längre. Assistenten startar inga egna
betalprov. Dockeråtkomst nekas i den aktuella sessionen, så kodändringen
är ännu inte driftsatt och den körande 0.8.166 kan fortfarande blockera jobb.

Återgång till Windows-upplägget utreds separat: före 0.8.153 hyrdes huvud-
Whisper, medan Qwen/Gemma/reservkontroller använde lokala miljöer. Det skiljer
sig från hela kedjan i molnet. Att bara ta bort spärren förkortar inte de
nuvarande stora installationerna/nedladdningarna. Äldre QA-fält kan inte
återställas blint: RemoteWhisperModel gav fabricerade sannolikheter.

## Historiskt: spärr och färdig molnimage (0.8.166, avvisad av användaren)

Molnhyra är spärrad tills en färdig image kan verifieras före hyra. Detta
ersätter uppstartens installationer och modellhämtningar på betald server.
Den faktiska SUBSYNQ_CLOUD_IMAGE är tom; inga nya betalprov ska startas.

Dockerfile.cloud paketerar separata redan byggda CUDA-miljöer för Whisper
och Qwen, Ollama och lokalt befintliga modeller. Den innehåller ingen
appconfig, API-nyckel, certifikat/privatnyckel, media eller SSH-värdnyckel.
Engångscertifikatet genereras fortfarande per jobb och pinnar HTTPS.
Prepared-bootstrap kontrollerar modellfiler och startar backenderna utan
installation/hämtning. Qwen och Whisper använder sina respektive offline-cacher.

Bygg med befintliga verifierade CUDA-imager och modellcache:

```bash
docker build -f Dockerfile.cloud --build-context models=./data/models -t subsynq-cloud:prepared .
```

Detta läser modellcachen och skapar en separat image; det ändrar inte modellerna.
De lokala källimagerna heter subsynq-qwen-cuda:local,
subsynq-ai-cuda:local och ollama/ollama:latest. Ange motsvarande
QWEN_IMAGE/WHISPER_IMAGE/OLLAMA_IMAGE via build-arg i en annan byggmiljö;
låsta käll-digester behövs vid distribution av en bestämd release.

Verifierat utan hyra: nätverket avstängt, verklig HTTPS-health ready efter
10,6 s på CPU, Gemma3/4 12B synliga i Ollama, laddad Whisper v3 och workerns
Qwen-hälsoväg fungerande. Verklig Qwen ASR+aligner laddades offline på
CPU/bfloat16 och Whisper v2 separat. CUDA-biblioteken är med, men faktisk
GPU-inferens har inte provats. Imagen är cirka 53 GB okomprimerad.
Gemma3/4 12B genererade också korta svar offline på CPU i en separat tillfällig
container med 12 GiB-tak och avlastning mellan modellerna. Det verifierar att
program och modellfiler fungerar tillsammans, inte undertextkvalitet.
665 tester passerar mot kod och färdig worker-image; 0.8.166 är frisk.

Distribution återstår: användaren har tillfrågats om registeradress, men ingen
adress/skrivbehörighet finns ännu. Publicera inte kod/modeller offentligt utan
användarens val. Efter publicering används ett immutable register/repo@sha256-
digest i SUBSYNQ_CLOUD_IMAGE. Workern läser manifest/config och verifierar
digester, Linux/amd64 och etiketten org.subsynq.cloud.prepared=1 före hyra.
Anonym Bearer-pull stöds; privata register som kräver autentisering spärras.
Se [Registry API V2](https://distribution.github.io/distribution/spec/api/).

Ingen image har publicerats och variabeln har inte fyllts i. Att en ny GPU-värd
kan hämta/köra imagen är fortfarande overifierat. En kall imagehämtning kan
ta tid och kosta; den lokala kontrollen bevisar inte gratis eller snabb start
hos Vast/Runpod. Hyresspärren ska därför ligga kvar medan detta återstår.

Status 2026-09-24: steg 2 korrigerat i 0.8.76. Inställningar har nu en egen
Moln-GPU-flik med **publika pris- och tillgänglighetslistor utan konto**,
valfri självbetjäning för separata Vast.ai-/Runpod-API-nycklar och en manuellt
ändringsbar kostnadsplan. De låsta molnvalen i hårdvarulistan är oförändrade.
Ingen molninstans är ansluten och inget molnjobb kan startas. Användaren har
ännu inga leverantörskonton, så autentiserade live-anrop är inte provade.
Den aktiva installationen använder CPU; lokal AMD-GPU är pausad. Kvalitetskontrollen
i 0.8.72 är driftsatt och provad separat.

## Avsedd användning

Inställningar ska visa Automatiskt, CPU, lokal AMD-GPU samt moln-GPU. Molnvalet
ska ange leverantör och GPU-modell, till exempel Vast.ai och RTX 5060 Ti. Ett
ej anslutet eller ej verifierat molnval får inte kunna starta ett jobb. Att spara
valet hyr inte en GPU.

Två arbetssätt behövs:

- **Test:** kör en vald kort fil och visa tid, transkript och uppskattad kostnad.
  Användaren stoppar/hyr av instansen separat tills säker automatisk livscykel
  finns.
- **Permanent:** återanvänd en ansluten instans för flera jobb; visa att
  beräkning och lagring kan kosta även när SubSynq står still.

Första implementationen bör fjärrköra Whisper-avlyssningen av ljudfönster.
Bibliotek, databas, översättning, kvalitetskontroll och SRT stannar lokalt.
Detta minskar mängden data som skickas men ljud lämnar fortfarande datorn.
Qwen och Whisper-v2:s kompletterande kontroller måste antingen köras lokalt
som idag eller byggas som särskilda fjärranrop och jämföras separat. Visa exakt
vilka delsteg som faktiskt använde moln-GPU.

## Kostnad i gränssnittet

Användaren kan ändra antaget GPU-pris per timme, lagringspris och
överföringskostnad när som helst. Nya priser anges endast i USD, samma valuta
som leverantörernas erbjudanden; någon automatisk valutaväxling finns inte.
Äldre sparade planer i SEK eller EUR varnas för och kan inte feltolkas som USD.
En lokal profil sparas med vald
leverantör och GPU. Beräknad GPU- och lagringskostnad för angiven tid visas.
Ett aktuellt erbjudande från respektive leverantör kan kopieras till planen,
men sparandet hyr inget. Vast.ai visar ett urval av billigaste en-GPU-erbjudanden
eller exakt modellsökning; Runpod visar bara GPU-typer med angivet pris och lager.
De läsande API-frågorna har verifierats utan nyckel från SubSynqs container.
Vasts API kräver modellnamn med blanksteg (t.ex. `RTX 5060 Ti`) för exakt sökning;
understreck gav inga träffar i kontrollen. Ingen statisk GPU-lista används.
Resultatet tidsstämplas och går att uppdatera på begäran. Återstår inför riktig
molnkörning: faktisk körd tid, mätt datamängd, lagringskostnad efter stopp och
en aktiv kostnadsgräns. Uppskattningen får aldrig kallas faktisk faktura.

Vast.ai tar enligt sin dokumentation betalt för beräkning när instansen kör,
lagring medan den finns och bandbredd. Att stoppa en instans stoppar alltså
inte lagringskostnaden. Leverantörens marknadspris kan ändras och ska hämtas
färskt för ett verkligt hyresbeslut.

## Första praktiska anslutning

1. Börja med Vast.ai eftersom användaren särskilt vill prova RTX 5060 Ti.
   Runpod finns i priskatalogen, men båda behöver fortfarande en skyddad
   ASR-koppling innan de får väljas som körande hårdvara.
2. Välj kompatibel CUDA-miljö på RTX 5-serien: Vast.ai anger CUDA 12.8 och
   PyTorch 2.7 eller senare för sina rekommenderade mallar. Verifiera själva
   Whisper- och Qwen-installationen på vald GPU innan den visas som redo.
3. Kör SubSynqs ASR-tjänst bakom en autentiserad, krypterad anslutning. Den
   befintliga lokala ROCm-tjänsten är inte avsedd att öppnas mot internet.
   Nycklar ska ligga utanför Git och aldrig visas i loggar eller status-API.
4. Gör ett jämförbart prov med samma ljud på CPU, lokal GPU när den aktiveras,
   och moln-GPU. Mät laddtid, faktisk bearbetningstid, överförd datamängd,
   minnesanvändning och avlyssningens kvalitet. En snabbare körning är inte
   automatiskt en bättre undertext.
5. Skilj uppkopplingsfel från underkänd undertext. Ingen tyst växling till CPU
   eller automatisk ny hyra när ett valt molnjobb inte kan köras.

Källor: [Vast.ai RTX 5-serien](https://github.com/vast-ai/docs/blob/main/rtx-5-series.mdx),
[Vast.ai kostnader](https://github.com/vast-ai/docs/blob/main/guides/pricing.mdx),
[Vast.ai instanshantering](https://github.com/vast-ai/docs/blob/main/guides/instances/manage-instances.mdx).

## Moln-CPU: hela AI-kedjan på en hyrd server (0.8.153)

Valet `cloud-cpu-vast` hyr en CPU-only-instans hos Vast.ai (`num_gpus: 0`,
ca 0,01 USD/h) och kör Whisper large-v3 (reserv: large-v2), Qwen3-ASR och
Gemma/Ollama där. **Kodat och enhetstestat, aldrig körts mot en riktig
instans.** Ingen betalning har gjorts; första riktiga prov kräver uttryckligt
klartecken för kostnaden.

- **En port, en token:** `ai/cloud_cpu_service.py` exponerar bara port 8766. Alla
  sökvägar måste börja med `/<token>/` (annars 404). `/transcribe` kör Whisper,
  `/qwen/...` och `/ollama/...` vidarebefordras till de lokala tjänsterna på
  servern. Token genereras per jobb och ligger i boot-skriptet. Trafiken är
  okrypterad HTTP: ljud lämnar datorn och kan avlyssnas på vägen.
- **Hela kedjan:** workern pekar `TRANSLATE_URL`/`QWEN_ASR_URL` mot servern under
  jobbet, och `RemoteCpuWhisperModel` skickar samma faster-whisper-argument som
  lokal CPU. large-v2-fragmentkontrollen, luckläsningen och reservpasset efter
  underkänd large-v3 går också dit; `SequentialASRModels.create_whisper` skapar
  aldrig lokala vikter för en fjärrmodell.
- **Ingen tyst reserv:** `/health` är `ready` först när Whisper, Qwen och alla
  Ollama-modeller svarar och minnet räcker (minst 16 GiB). Annars väntar workern
  (max 45 min) eller stoppar med tekniskt fel, och instansen förstörs alltid.
- **Känd osäkerhet:** Vast anger `cpu_ram: 0` för CPU-erbjudanden, så minne kan
  inte filtreras före hyra. Tjänsten mäter sin egen gräns (cgroup/MemTotal) och
  stoppar jobbet om den är för liten. Urvalet kräver 16 kärnor, 80 GB disk,
  300 Mbit/s ner och tillförlitlighet 0,95, billigast först.
- **Overifierat:** bildvalet `ubuntu:24.04` med apt/pip/Ollama-installation i
  boot-skriptet, hur lång uppstarten blir (cirka 20 GB modellnedladdning),
  att port 8766 mappas på CPU-instanser, och Whisper/Qwen-hastigheten på en
  delad CPU. Mät allt detta vid första provet.

### Moln-GPU kör nu också hela kedjan (0.8.155)

`cloud-vast` (moln-GPU) använder samma gateway som moln-CPU med
`SUBSYNQ_CLOUD_DEVICE=cuda`: Whisper large-v3/v2 i faster-whisper (float16),
Qwen3-ASR på CUDA (float16) och Gemma via Ollama på GPU, alla på den hyrda
servern. Den gamla vägen (bara Whisper via `ai/gpu_service.py`, Qwen/Gemma lokalt)
är borttagen ur workern (`rent_cloud_gpu`); `gpu_service.py` används fortfarande
för den lokala AMD-GPU:n. Bilden är `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`
(cuBLAS/cuDNN för CTranslate2, Blackwell-kapabel) med CUDA-hjul av torch 2.9.1.
`/health` blir inte `ready` om `nvidia-smi` saknar GPU (`no_gpu`) eller har
under 15 GiB VRAM (`insufficient_vram`), så Ollama/Qwen kan aldrig i tysthet
falla tillbaka på CPU. **Overifierat:** bildtaggen, att torch-cu128/CTranslate2
fungerar på RTX 5060 Ti (Blackwell), att Ollama faktiskt använder GPU:n, och
VRAM-marginalen för Gemma 12B med reservmodellen. Inget betalprov är gjort.

### Krypterad trafik (HTTPS, 0.8.157)

Trafiken mellan workern och molnservern är HTTPS. Workern skapar ett nytt
självsignerat EC-certifikat (P-256, giltigt två dygn) per hyra med `openssl`
och skickar certifikat och nyckel i boot-skriptet över leverantörens HTTPS-API.
Gatewayen (`ai/cloud_cpu_service.py`) startar bara med certifikat och har inget
okrypterat läge. Workern litar på exakt detta certifikat (pinning) och inget
annat, så varken systemets certifikatlista eller någon annan server godtas; ett
certifikatfel stoppar jobbet direkt och förstör instansen. Ljud, dialogtext och
filnamn är därmed krypterade på vägen. Leverantören själv (Vast.ai) och
serverns värd kan fortfarande se innehållet och nyckeln. Overifierat mot en
riktig instans (certifikatet skrivs med `umask 077` i boot-skriptet).

### Runpod.io (0.8.158)

Samma helkedjetjänst kan hyras hos Runpod via deras REST-API (`rest.runpod.io/v1`,
`cloud_gpu.runpod_create_pod`/`runpod_show_pod`/`runpod_destroy_pod`) bakom en
leverantörsneutral fasad (`create_chain_instance` m.fl.) som `worker.rent_cloud_chain`
använder för både Vast.ai och Runpod. Valen är `cloud-runpod` (GPU) och
`cloud-cpu-runpod` (CPU).

- **GPU:** Runpod saknar RTX 5060 Ti, så det billigaste lediga NVIDIA-kortet i Secure
  Cloud med minst 16 GB VRAM hyrs i stället för den sparade kostnadsprofilens GPU
  (läst live 2026-10-08: RTX A5000 24 GB ≈ 0,16 USD/h, därefter RTX 4000 Ada,
  RTX 3090, RTX 4090). Gatewayen mäter VRAM själv och stoppar vid under 15 GiB.
- **CPU:** flavor `cpu3m` med 8 vCPU. CPU-poddar prissätts först vid skapandet; priset
  läses ur svaret (`costPerHr`). Antagandet att `cpu3m` ger minst 16 GiB RAM är
  overifierat; gatewayen stoppar jobbet om minnet är för litet.
- **Nätverk:** TCP-port 8766 med publik IP (`supportPublicIp`, Secure Cloud). Runpods HTTP-
  proxy används inte: den bryter alla anrop efter 100 sekunder (Cloudflare), vilket är för
  kort för Whisper/Gemma. Samma HTTPS med pinnat certifikat som hos Vast.
- **Start:** Runpod kör poddens kommando som huvudprocess, så boot-skriptet skickas som
  `dockerStartCmd` och avslutas med `tail -f` för att hålla containern vid liv.
- **Skydd:** hyran får aldrig överstiga 2 USD/h (stoppar och förstör annars), adressen
  måste finnas inom 20 minuter, och instansen förstörs (DELETE /pods/{id}) vid varje fel.
  Jobbraden sparar `runpod:<id>` så en omstartad worker kan städa rätt leverantör.
- **Verifierat live (läsning, ingen hyra):** nyckeln autentiserar mot REST, `GET /pods`
  ger `[]`, GPU-katalogen och `cpuFlavors` läses. **Overifierat:** att en pod faktiskt skapas
  med dessa fält, att CPU-poddar tillåter egen bild, `dockerStartCmd` och publik TCP-port, och
  att `portMappings` fylls i för port 8766. Ingen pod har skapats.

### VIKTIGT: tomt filnamn till Gemma i molnläget (0.8.159)

I molnläget skickas `media_name` tomt i källgranskningen; språkhinten räknas
fortfarande lokalt från det riktiga namnet och skickas med. Effekten på
granskarens övriga bedömningar är ej mätt (HOPE, KING, Sigma, PLANET 9 ej
jämförda med och utan namn). Om ett molnjobb stoppas av källkvaliteten men
samma fil klarar sig lokalt: misstänk först detta.

### Felsökning av en server som inte blir redo (0.8.162)

Första riktiga moln-GPU-körningen gav "blev inte redo inom tiden" efter 45 minuter utan att
visa varför. Nu rapporterar servern själv: boot-skriptet skriver aktuellt steg till
`/root/step` och ett fel till `/root/failed` (ERR-trap), och en diagnostik-gateway startar
direkt efter `apt` (kräver bara systemets python3) och svarar på `/health` (med
`bootstrap_step`, `bootstrap_failed`) och `/log` (slutet av bootstrap-, Ollama-, Qwen- och
gateway-loggarna) tills den riktiga gatewayen ersätter den. Workern visar steget i jobbets
statusrad, stoppar direkt vid `bootstrap_failed`, och vid timeout tas sista steget och
serverns logg med i felmeddelandet (och i workerns logg). Svarar servern inte alls inom 15
minuter avbryts hyran tidigt och Vasts egen containerlogg försöks hämtas
(`request_logs`). Bootstrap-skriptet ligger nu också i det packade paketet (skriptet är
cirka 13 500 tecken; gränsen hos Vast är 16 384).

Lokal repetition (ren Ubuntu 24.04-container, utan GPU, med en liten Ollama-modell): hela
flödet blev `ready` på cirka sex minuter (apt, båda venv, Ollama, Whisper-nedladdning,
HTTPS-gateway, Qwen, Ollama) och Whisper large-v3 och large-v2, Qwen och Ollama svarade
genom gatewayen; fel token gav 404 och vanlig HTTP fick inget svar. Pip-stegen för både CPU
(torch 2.9.1+cpu) och GPU (torch 2.9.1+cu128, CTranslate2 4.8.2) installerades utan fel.
Det som inte kunnat repeteras utan riktig hyra: Vasts onstart-körning i `nvidia/cuda`-bilden,
GPU-delen (Ollama/Qwen/Whisper på CUDA) och nedladdningstiden på en hyrd värd.

### Timeout under paketinstallation (0.8.163)

Den senaste serverloggen visar `ReadTimeoutError` från `pypi.nvidia.com` i
steget `pip: Qwen-miljö (torch)`. Pip avbröt en nedladdning före modellstart;
loggen avgör inte om orsaken var värdens anslutning eller paketservern.

Alla tre pip-installationer använder nu 120 sekunders socket-timeout,
`--retries 5` och `/root/.cache/pip`. Vid ett transportfel körs hela
installationskommandot igen, högst tre gånger totalt, med 15 respektive 30
sekunders väntan. Cachen kan återanvända färdiga nedladdningar, men delvis
hämtade filer garanteras inte återupptas. Paketpinnar, CPU/cu128-index,
HTTPS och kvalitetskontroller är oförändrade. Paket-/certifikatfel stoppas
direkt; bestående nätverksfel rapporteras genom befintlig felmarkör och logg.
Den totala beredskapstiden är fortsatt 45 minuter.

Verifierat utan hyra: 653 tester mot källkod och färdig worker-image, verkliga
Bash-prov med simulerad pip och syntaxkontroll av CPU/CUDA-bootstrap.
Startskripten med riktiga engångscertifikat mätte 14 091 tecken (under 16 000).
0.8.163 är driftsatt och frisk. Ingen ny betald server har startats;
verklig nedladdning och hela CUDA-kedjan behöver fortfarande verifieras på
en hyrd värd efter användarens kostnadsgodkännande.

### Qwen-kontrollen efter färdig uppstart (0.8.164)

Jobb 6 kom förbi installationen och Gemma-hämtningen men stoppades med
`Qwen3-ASR blev inte redo efter väntan`. Gatewayens samlade `/health` kunde
rapportera ready genom att fråga Qwen direkt på molnservern, medan workerns
senare `/<token>/qwen/health` fick 404: den GET-vägen saknades. Felet fanns
redan i molnkedjans införande 0.8.153, före HTTPS-ändringen.

Gatewayen vidarebefordrar nu just denna GET-väg till Qwens `/health`.
Tokenkontroll och pinnad HTTPS gäller fortsatt. Integrationstester verifierar
workerns riktiga kontroll genom HTTPS, att backendfel stoppar och att fel
token inte når Qwen. Totalt 657 tester passerar mot kod och färdig image.
Ingen ny instans hyrdes för verifieringen; verklig CUDA-inferens återstår.

Jobbets Qwen-/v2-etiketter säger nu moln-GPU eller moln-CPU efter körvalet
i stället för hårdkodad CPU. Väntestatusen anger även molnval och leverantör.
Lokal-motor-kortets CPU/GPU-mätning gäller fortfarande enbart den lokala
maskinen och visar inte den hyrda serverns belastning.

### Totalbudget nådd under Whisper-hämtningen (0.8.165)

Jobb 7 startade 22:01 men Whisper-hämtningen började först 22:32:24.
Den totala 45-minutersgränsen lämnade då cirka 14 minuter för modellhämtningen.
Qwen svarade 200; Ollamas /api/tags svarade, men det betyder inte att Gemma
var hämtad. HF_TOKEN-varningen är inte ett belägg för ett tokenkrav.

Whisper-hämtningen använde faster-whispers avstängda progressbar. Nu anges
large-v3 respektive large-v2 i bootstrap_step och normal HF-progress loggas
i bootstrap.log. Snapshot_download använder samma Systran-repon och samma
filfilter som faster-whisper, med max_workers=1. HF_HUB_DISABLE_XET=1 och
HF_HUB_DOWNLOAD_TIMEOUT=120 sätts före import. HTTP-hämtning fungerade i
tidigare Linux-prov, men loggen bevisar inte att Xet orsakade molnstoppet.
Se [Hugging Faces miljövariabler](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables)
och [faster-whispers hämtkod](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/utils.py).

Cache/modeller och den totala 45-minutersgränsen behålls. Ingen HF-token
läggs till och ingen ny instans hyrs som del av verifieringen. Verklig
nedladdningshastighet på den hyrda värden och hela CUDA-kedjan återstår att
verifiera; ändringen är inte en garanti att nästa värd hinner bli redo.

### VRAM-mätning för att välja GPU (0.8.173)

På en hyrd GPU-server (`SUBSYNQ_CLOUD_DEVICE=cuda`) samplar gatewayen `nvidia-smi` var 0,5 s
(`VramMonitor` i `ai/cloud_cpu_service.py`) och knyter varje avläsning till det steg som körs:
`whisper-large-v3`/`whisper-large-v2`, `qwen-alignment`, `qwen-identify`, `ollama-<modell>`
(t.ex. `ollama-gemma4:12b`) och `idle` (det som ligger kvar mellan stegen, bl.a. Whisper som
stannar i minnet). Värdet är HELA kortets `memory.used`, alltså det ett köpt kort måste rymma,
inte bara en process. Per steg sparas toppen, antal förfrågningar och processerna vid toppen.
`GET /<token>/vram` ger rapporten. När jobbet slutar (även vid fel), innan instansen förstörs,
hämtar workern den, skriver en rad i workerns logg (`VRAM-topp (GiB, hela kortet): ...`) och
sparar hela rapporten som `/config/cloud-vram/job-<id>.json` (på Unraid:
`data/config/cloud-vram/`). Moln-CPU-servrar har ingen monitor.

Så väljer du kort: hyr ett kort med marginal (Runpod RTX A5000 24 GB, ~0,16 USD/h), kör ett helt
jobb, läs `högst` i raden. Välj det minsta kort vars VRAM ligger tydligt över den toppen.
Avläsningen är periodisk och kan missa en topp som är kortare än 0,5 s, så räkna med marginal
(minst ett par GiB). Toppen beror på ljudfönstrens längd, så mät på en riktig film, inte bara ett
klipp. Inget GPU-jobb har ännu körts klart, så det finns inga mätta siffror.

### Parallell uppstart och flera erbjudanden (0.8.174)

Boot-skriptet hämtar de ~40 GB paket och modeller i **fem parallella spår** i stället för en lång
kedja: `qwenpy` (torch + qwen-asr), `whisperpy` (faster-whisper), `ollama` (installera, starta, hämta
Gemma), `whispermodels` (Whisper v3/v2) och `qwenmodels` (Qwen ASR/aligner; hämtas nu redan här och
inte först i första förfrågan). Varje spår har egen `/root/step.<spår>` och `/root/failed.<spår>`;
`/health` visar alla ("ollama: hämtar gemma4:12b | qwenpy: pip: qwen-asr | ...") och ett fel i ett spår
stoppar jobbet direkt med spårets namn. Gatewayen startar när Python-miljöerna och modellerna är
klara; Ollama kan fortfarande hämta Gemma då, och hälsan är `ready` först när allt finns.
Shell-kommentarer tas bort ur kopian som skickas (`compact_shell`), skriptet är ~15 000 tecken (spärr 16 000).

Lokal repetition (ren ubuntu:24.04, CPU, liten Ollama-modell): `ready` på 201 s (förut ~360 s seriellt utan
Qwen-modellerna), kedjan svarade genom gatewayen, och ett fel i ett spår (modell som inte finns) gav
`bootstrap_failed` med "ollama: misslyckades vid: hämtar ..." efter 137 s. EJ provat på en riktig hyrd
värd; den verkliga värdens nätverk avgör, och det är den som var långsam på Vast.

Hyrflödet provar de fyra billigaste erbjudandena i tur och ordning när leverantören avvisar en beställning
(`CLOUD_OFFER_ATTEMPTS`): tunt lager (Runpod visar `Low` på alla billiga kort) och erbjudanden som hinner
tas gör att första försöket ofta misslyckas. En avvisad beställning skapar och kostar inget. Gratiskontroll
2026-10-09: Runpods schema godkänner alla fält i vår poddbeställning (bara det påhittade GPU-/CPU-namnet i
provet avvisades).

**Spara hämtningen mellan hyror** (ej byggt): Runpods nätverksdisk (`POST /networkvolumes`, Secure Cloud,
0,07 USD/GB/månad, ~50 GB ≈ 3,5 USD/månad) behåller modeller och Python-miljöer så att senare hyror
startar på minuter. Nackdelen är att disken är bunden till ETT datacenter, och i dag har bara 1-2 datacenter
lediga (Low) billiga kort var. Besluta efter att en verklig kall uppstart på Runpod är mätt.

### Värdar väljs på nätverkshastighet (0.8.175, Vast)

Ett helkedjejobb hämtar ~40 GB till den hyrda värden, så värdens nätverk avgör uppstartstiden (en mätt Vast-start
la 31 minuter på bara installationen). Moln-GPU hos Vast väljer därför fortfarande den GPU-MODELL du valt i Moln-GPU-
fliken (RTX 5060 Ti, 3090, 4090 ... valfri `gpu_name`), men bara bland värdar med minst 500 Mbit/s nedladdning,
tillförlitlighet 0,95 och 80 GB disk (`fetch_vast_chain_gpu_offers`); finns ingen sådan faller den ner till
300 Mbit/s, och finns ingen alls hyrs ingenting ("Inga lediga ... Inget hyrdes"). Filtren tillämpas både i Vasts sökning
och lokalt. Statusraden visar vald värds hastighet. Läst live 2026-10-09: RTX 5060 Ti 24 värdar (från 0,127 USD/h),
RTX 3090 17 (från 0,174), RTX 4090 37 (från 0,389), alla över 500 Mbit/s; billigaste värden utan filter kostade
~0,02 USD/h mindre men var ofta under 100 Mbit/s (en hade 15). Marknadssidan i webben och moln-CPU-vägen påverkas inte.
Overifierat: att en snabb värd faktiskt startar på kort tid; ingen riktig körning är gjord.

### Första lyckade moln-GPU-körningen (jobb 17, Vast, 2026-10-09)

Användaren startade en körning i webben (Ray Gunn trailer, 37 repliker, Vast RTX 5060 Ti 16 GB). Hela kedjan
(Whisper, Qwen, Gemma-översättning och Gemma4-slutgranskning) gick på den hyrda servern, instansen förstördes
automatiskt (0 kvar hos Vast) och VRAM-mätningen sparades. Jobbet föll ändå i översättningens slutkontroll
(kvalitetsspärr, se STATUS; orsaken är åtgärdad i 0.8.177 med namnregeln, inget molnfel), inte i molnet. Tider (UTC): skapat 16:27:50, första Gemma-anropet (källgranskningen,
alltså efter uppstart + nedladdning + Whisper/Qwen på trailern) 16:36:59, slut 16:45:25; kostnad ungefär 4 cent
(uppskattning). Statusraderna sparas inte, så uppstartstiden ensam är inte avläst. VRAM-toppar (hela kortet,
16 311 MiB): Gemma3 13 562, Gemma4 13 290, Qwen-alignment 9 977, Qwen-identify 8 605, Whisper v2 5 405, Whisper v3
4 160 MiB; `idle` 13 562 MiB (Gemma-processen 9 432 + Whisper-processen 3 888 stannar kvar). Ett 16 GB-kort räckte
för den här korta filen med ~2,4 GiB marginal; lång film och mätningens 0,5 s-intervall kan ge högre toppar.
