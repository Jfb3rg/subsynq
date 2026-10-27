# Alternativa ASR-prov – separat från produktion

`alternative_asr_probe.py` kör korta lokala ljudprov med Meta MMS eller
WhisperX samt NeMo Conformer-CTC. Det är INTE en ny produktionsbackend, kvalitetsgranskare eller
automatisk källrättare. Inga databas-/kö-/SRT-funktioner importeras.
Appversionen ändras inte av denna separata testmiljö.

## Testmiljö

Bygg `docker build -f Dockerfile.asr-probe -t subsynq-asr-probe:20260928 .`.
PyTorch/torchaudio/torchvision är CPU-paket; WhisperX 3.8.6, Transformers
4.57.6 och Silero 6.2.3 är valda för detta prov. Övriga indirekta paket är
inte fullständigt låsta. Imagen är inte kopplad till compose-tjänsterna.
Ingen diarization, HF-token, kontoanslutning eller molntjänst används.
Silero laddas från installerat paket, inte från en rörlig GitHub-gren.
NLTK:s meningsdelning laddas vid byggandet, aldrig under ljudprovet.

## Nedladdade modeller 2026-09-28

Efter de sex proven rensades **endast MMS-testmodellens mapp** på användarens
uttryckliga klartecken. WhisperX:s hindi-aligner, rapporter och gemensam testimage
finns kvar. Tabellen beskriver de provade revisionerna; MMS måste hämtas igen
för att köras på nytt. Ingen produktionsmodell togs bort.

| Modell | Revision | Hämtade vikter |
| --- | --- | --- |
| `facebook/mms-1b-all` | `3d33597edbdaaba14a8e858e2c8caa76e3cec0cd` | `model.safetensors`, `adapter.hin.safetensors` |
| `theainerd/Wav2Vec2-large-xlsr-hindi` | `062f7f566e2671336992b011dcb9387cd3cffe5e` | `model.safetensors` |

Båda fick dessutom endast `config.json`, `preprocessor_config.json`,
`special_tokens_map.json`, `tokenizer_config.json`, `vocab.json`.
Använd `huggingface_hub.snapshot_download` med dessa exakta revisioner,
`allow_patterns` och separata `local_dir`-mappar under
`data/models/diagnostics`. Hämta INTE hela MMS-repot med alla språkadaptrar.
Inga `.bin`-vikter eller `trust_remote_code` används.
MMS-viktens verifierade SHA256:
`0f1d95ce43d27e03d5d8dd56c697c805460f967c793dd2cbec2e8e8012deda98`.
Hindialignerns lokalt beräknade SHA256 (för framtida reproduktion, inte
ett separat externt verifierat licens-/säkerhetsintyg):
`c9469047fff51aa63e2ac40bdcef19ad5a5ab62fdfa04d14c05840e7398d1532`.

Modellkällor: https://huggingface.co/facebook/mms-1b-all och
https://huggingface.co/theainerd/Wav2Vec2-large-xlsr-hindi.
MMS har CC-BY-NC-4.0. Hindialignerns modellkort anger ingen separat tydlig
licens. Detta prov är INTE ett licensgodkännande för produktionsdistribution
eller kommersiell användning. WhisperX: https://github.com/m-bain/whisperX.

## Körning

Montera repot vid `/probe:ro`, originalmedier vid `/media:ro`, befintliga
modeller vid `/models:ro`, samt ENDAST en ny diagnostikmapp vid `/results`.
Kör med `--network none`, `--cpus 6`, `--memory 8g`, `HF_HUB_OFFLINE=1`.
Exempel på argument efter image-namnet:

```text
/media/VALD_FIL.mp4 --engine mms --model /models/diagnostics/mms-1b-all --mms-language hin --start 0 --end 25 --output /results/mms-new.json
/media/VALD_FIL.mp4 --engine whisperx --model /models/LOKAL_WHISPER_SNAPSHOT --language hi --align-model /models/diagnostics/whisperx-hindi-align --start 0 --end 25 --output /results/whisperx-new.json
```

WhisperX använder redan hämtad faster-whisper large-v3, CPU int8,
batch_size 1, standard beam 5 och Silero-talavgränsning. Det är samma
Whisper-modellfamilj som tidigare, men annan avgränsning/avkodning.
Alignment körs EFTER avlyssning och lägger till ordtider; det bevisar inte
att de angivna orden eller betydelsen är rätt. Tider i rapporten är relativa
till valt klipp; lägg till `start` för originalets tidsaxel.

Ljudfönster måste vara ändliga, positiva och högst 60 s. `--silence`
genererar ett helt tyst kontrollklipp av samma längd utan att läsa mediet.
Ljudkopior finns bara i temporär katalog som tas bort efter provet.
`--output` måste vara en NY diagnostikfil (existerande filer skrivs inte
över); peka aldrig på produktionsrapporterna. Slutrapport skrivs först
efter att alla valda steg lyckats; vid tekniskt fel finns loggen, inte ett
falskt godkänt resultat. Inga prov är mänskligt verifierade transkriptioner.

## NVIDIA Hindi/Hinglish Conformer – separat CPU-prov

`Dockerfile.nemo-probe` bygger vidare på testimagen, INTE någon
produktionsimage. NeMo **3.0.0**, Transformers **4.57.6** och CPU-PyTorch
**2.8.0+cpu**. NeMo installerar även CUDA-bindings som paketberoende, men
PyTorch har ingen CUDA/HIP-runtime och provet är CPU. `pip check` passerade.
Inga experimentloggare eller nätverk används under avlyssningen.

Modell: `nvidia/nemo/stt_hi_conformer_ctc_large`, NGC-version **1.10**,
källa https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/stt_hi_conformer_ctc_large.
Modellvikter omfattas av NGC-villkor, inte automatiskt NeMo-kodens licens.
Ingen permanent produktionsdistribution vald.

Endast `stt_hi_conformer_ctc_large.nemo` (**461 393 920 byte**) hämtades till
`data/models/diagnostics/nvidia-hi-conformer-ctc-large-1.10/`.
KenLM-filen (**1 762 879 824 byte**) hämtades INTE. NGC API:s file-endpoint
ger ett 200-svar med JSON och den verkliga filadressen i `Location`-headern;
följ den adressen. Behandla inte JSON-svaret som en modell. Kontrollera
filstorlek efter hämtning. Lokalt beräknad SHA256 för återupprepning:
`53a8c8cc4e77a34c67ba20e94d003ad08dc49acf01b57992f02c66f5bf5b0280`.
Detta är inte en oberoende signerad checksumma.

Bygg `docker build -f Dockerfile.nemo-probe -t subsynq-nemo-probe:20260928 .`.
Montera endast `ai/` vid `/probe/ai:ro`, vald modellmapp vid `/models:ro`,
media vid `/media:ro` och en separat diagnostikmapp vid `/results`. Kör med
`--network none`, `--cpus 6`, `--memory 8g`. Exempel på probe-argument:

```text
/media/VALD_FIL.mp4 --engine nemo --model /models/stt_hi_conformer_ctc_large.nemo --start 0 --end 25 --check-forward --output /results/nvidia-new.json
```

Lokala vikter laddas med `restore_from(..., map_location=cpu)`, inte
`from_pretrained`. Språket bestäms av checkpointen; `--language` väljer inte
om en Hindi-modell till ett annat språk. Decoding är checkpointens `greedy`,
batch_size 1, num_workers 0, ingen KenLM eller VAD. Originalmedier och
produktionens config/API-nycklar behövs inte inne i testcontainern.

`--check-forward` observerar antal prover som faktiskt når modellen och
jämför mot extraherat ljud. Därefter jämförs direkt CTC-inferens med vanlig
`transcribe`. Återställ `eval` och använd samma dither=0/pad_to=0 som
NeMo använder vid transkribering. I vår version satte NeMo efterarbetet
encodern i träningsläge trots att modellens ursprungsläge var eval. Ett
direktprov utan återställda inställningar gav annan text och är INTE en
rättvis jämförelse. Provrapporter markerar inställningar och faktisk
överensstämmelse; det är aldrig ett intyg om rätt ord eller betydelse.

## Kontrollerad kanal-/filterjämförelse med befintliga Qwen och Whisper

`ai/audio_channel_probe.py` är ett fristående, generellt diagnosverktyg.
Ingen filnamnsregel, worker-import, produktionsrapport, SRT eller databas används.
Modeller hämtas INTE; båda läses från lokala cache-snapshots, offline på CPU.
Kör tunga prov efter varandra för att undvika samtidig modellbelastning.

Tre faser (exempelargument, inte produktionsinställningar):

```text
prepare --media /media/VALD_FIL.mp4 --samples /scratch/short --start 12.8 --end 18.8 --output /results/short-audio.json
qwen --samples /scratch/short --model /models/LOKAL_QWEN_SNAPSHOT --output /results/short-qwen.json
whisper --samples /scratch/short --model /models/LOKAL_WHISPER_SNAPSHOT --language hi --output /results/short-whisper.json
```

`prepare` körs i befintliga `subsynq-ai:local` (ffmpeg/numpy), Qwen-fasen i
`subsynq-qwen3-asr:0.8.59`, Whisper-fasen i `subsynq-ai:local`.
Montera aktuell `ai/` vid `/probe/ai:ro`, endast vald mediamapp vid `/media:ro`,
modeller vid `/models:ro`, separat diagnostikoutput vid `/results` och en NY
temporär Docker-volym vid `/scratch`. Förberedelsen behöver skrivbar scratch;
inferens läser den med `:ro` och behöver ingen mediamapp. Ingen config,
API-nyckel, Docker-socket eller produktionsoutput behövs. `prepare` kan kräva
`--user 0` för en ny tom volym. Använd alltid `--network none`, sex CPU:er,
`OMP_NUM_THREADS=6`/`MKL_NUM_THREADS=6` för Qwen. Här användes 8 GB för korta
prov och Whisper, 11 GB för längre Qwen-prov; inte GPU eller moln.

Första ljudspåret måste vara stereo. Positivt fönster, högst 60 s; extraherad
längd måste matcha fönstret. En ny samples-mapp och ny rapport krävs: tidigare
ljud/rapporter får inte skrivas över. SHA256 och manifest kontrolleras före
inferens. Mono 16 kHz PCM16: originalets vanliga ffmpeg-downmix, vänster,
höger, `highpass=f=80,lowpass=f=7500` och `afftdn=nr=6:nf=-45`.
Filtren körs på stereoljudet före downmix; brusreducering är INTE separation
av dialog från musik. `--variants mono left right` begränsar förberedelsen;
inferens använder alla varianter i manifestet. Qwen kör auto-språk/float32,
max_new_tokens 256 utan aligner; Whisper int8, beam 5, temperature 0,
ingen VAD, ingen föregående-text-konditionering, ordtider. Whisper-språk
kan anges explicit; om det utelämnas är det auto. Ingen förväntad replik ges.

Rapporter innehåller signalmått, WAV-hashar, text, tider och Whisper-ord.
PCM-limit/float-fullskaleöverträdelser är signalmått, inte bevis för att
ursprunglig AAC-kodning varit klippt. Filter kan själva ge fullskalevärden;
mest flytande text eller hög ordsannolikhet är INTE ett Hindi-facit.
Bevara JSON-rapporter och rensa endast den egna temporära ljudvolymen
efter kontroll av exakt namn och inga beroende containrar. Originalmedia
och modellvikter förblir orörda. Resultat/avgränsningar i senaste `STATUS.md`.

Filterdokumentation: https://ffmpeg.org/ffmpeg-filters.html#pan-1,
https://ffmpeg.org/ffmpeg-filters.html#highpass och
https://ffmpeg.org/ffmpeg-filters.html#afftdn.

## Demucs vokal-/musikseparation – fristående prov

`Dockerfile.demucs-probe` bygger på befintlig CPU-testimage och installerar
Demucs **4.0.1**; `pip check` passerar, CPU-PyTorch **2.8.0+cpu** bevaras.
Ingen produktionsimage/tjänst ändras. `ai/separation_probe.py` tar ett positivt
fönster högst 60 s, lokal `--repo`, ny `--samples` och ny `--output`.
Exempel: `--media /media/VALD_FIL.mp4 --repo /models --samples /results/audio
--output /results/reports/separation.json --start 0 --end 25`.
Montera kod, media och modell skrivskyddat; endast separat diagnostikoutput
skrivbar. CPU sex trådar, 8 GB, `--network none`, ingen config/API-nyckel/socket.

Officiell enkel HTDemucs-checkpoint:
https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th,
**84 141 911 byte**. Full lokal SHA256:
`8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4`.
Officiella filnamnets hashprefix kontrolleras före inläsning; hela lokala
hashen är inte en oberoende signerad checksumma. Äldre Demucs-pickle
kräver `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` med PyTorch 2.8: ENDAST denna
officiella checkpoint i isolerad container utan nätverk/hemligheter.
Använd inte denna inläsningsväg för godtyckliga externa checkpoint-filer.

Demucs standardnormalisering, `eval`, shifts=0, split, overlap=0.25,
modellsegment 7.8 s. Källfönstrets längd bevaras. Vokaler sparas som FLOAT
stereo 44,1 kHz, sedan mono PCM16/16 kHz med explicit gain för headroom.
Originalmix extraheras separat utan separation. Manifest har båda WAV-hashar;
`audio_channel_probe.py` accepterar `vocals` vid inferens men erbjuder det
INTE som vanlig ffmpeg-filtervariant. Kör Qwen/Whisper på exakt samma två
WAV:er med samma inställningar som kanalprovet. Separering tränad för
musikvokaler är INTE garanterad dialogisolering eller facit för ASR/tider.
Demucs ursprungliga repo är arkiverat och detta är diagnostik, inget
permanent/licensmässigt produktionsval: https://github.com/facebookresearch/demucs.
Lyckade testkopior och rapporter bevaras separat för lyssning/uppföljning.

## Hindi-finjusterad Whisper och direkt tystnadskontroll

Vasista22-checkpoint: `vasista22/whisper-hindi-large-v2`, revision
`f98540cee2b8cba5b3f7d1b123608dda0a5f5a95`, Apache-2.0 enligt utgivaren.
https://huggingface.co/vasista22/whisper-hindi-large-v2.
Hämta bara `pytorch_model.bin` och processor-/tokenizer-/configfilerna;
JAX-vikter och `training_args.bin` behövs inte. Vikter **6 173 647 530 byte**,
SHA256 kontrollerad mot utgivarens LFS-oid på denna revision:
`4202d803d6855f2d147a2dd657b968a9680633d879cf41a13976d98159d21883`.
Ingen kod från modellrepo exekveras eller `trust_remote_code` aktiveras.

Separat offline-konvertering i CPU-testimagen, source skrivskyddad,
ny outputmapp, sex CPU:er/20 GB och `HF_HUB_OFFLINE=1`:

```text
ct2-transformers-converter --model /source --output_dir /converted/int8 --quantization int8 --low_cpu_mem_usage --copy_files preprocessor_config.json tokenizer_config.json
```

Ingen `--force`, ingen produktionsmodell ersätts. Originalvikter och
konverterat format bevaras separat för återupprepning. Konverteringsstöd:
https://opennmt.net/CTranslate2/guides/transformers.html.

Äldre Whisper-tokenizers kan sakna `tokenizer.json` och de 1 501 standard-
tidsstämplingstokensen. Faster-whispers automatiska tokenizerhämtning ska
INTE användas för denna offline-jämförelse. Kör vid behov generella
`ai/whisper_tokenizer_conversion.py --source /source --converted /converted/int8`
med source skrivskyddad och endast NY diagnostikkonvertering skrivbar.
Verktyget tillåter enbart det fullständiga standard-suffixet `<|0.00|>`
till `<|30.00|>` i 20 ms-steg. Alla befintliga token-ID måste matcha;
hela slutliga vocabularyn måste vara identisk med CT2-modellens lista.
Okända/omordnade/partiella tokens och tidigare tokenizerfil avvisas.
Vasista22 hade 50 364 källtokens, CT2 51 865; alla slutliga ID verifierades.

Kör samma `audio_channel_probe.py whisper` som tidigare med explicit `hi`,
CPU int8/beam 5/temperature 0/ingen VAD/föregående text, och exakt samma
sparade original-/vokal-WAV. Modellkortets finjustering är ingen garanti
för trailerljud; ingen förväntad replik ges som prompt eller facit.

Generell ny fas `silence` skapar ENBART genererade nollprover i ny mapp:
`silence --samples /results/silence --start 0 --end 4 --output /results/reports/silence-audio.json`.
Kräver inte media eller modell, validerar positivt fönster högst 60 s och
start=0, vägrar ersätta mapp/rapport. Mono PCM16/16 kHz och hashmanifest.
Kör därefter `whisper --samples /samples --model /models/int8 --language hi
--output /results/silence-asr.json` på kontrollmappen skrivskyddat. Detta är
DIREKT ASR utan extern VAD, inte en kontroll där VAD gömmer modellens svar.
Rapportens `silence_control` skiljer kontrollen från dialog. Tomt svar är
väntat; text från tystnad är en varningssignal, aldrig godkänd undertext.

`--without-timestamps` är ett EXPLICIT generellt Whisper-diagnosläge:
ren textavlyssning utan ordtider/extern VAD. Beam 5, temperature 0,
språkhint och övrig dialogbehandling oförändrade. Standardläget för
tidigare prov är oförändrat. Fönstrets start/slut i textläget är inte
verifierade undertexttider. Inget av dessa lägen aktiveras i produktion.

Om konverteringens betydelse behöver skiljas från checkpointen, använd
`ai/hf_whisper_probe.py --model /models --samples /samples --language hi
--output /results/NY_RAPPORT.json` i befintlig CPU-testimage. Native
Transformers 4.57.6/PyTorch 2.8.0+cpu, lokal processor och originalvikter,
`weights_only=True`, float32/eval/inference_mode, sex trådar/12 GB,
explicit Hindi/transcribe, beam 5, ingen sampling/tidsstämpling/textprompt,
max_new_tokens 256 och inference-cache på. Källa/config/ljud skrivskyddade,
nätverk av och separat output; samma WAV-hashar och feature-shape loggas.
Ingen extern VAD. Native-generering och faster-whispers inbyggda
no-speech-/suppressionhantering är inte identiska; detta är en referens,
inte en rättvis hastighetsbenchmark eller verifierad Hindi-transkription.
Äldre generation_config kan sakna language/task/no-timestamps-mappning.
Referensverktyget kompletterar ENBART saknade fält i minnet från lokala,
kontrollerade token-ID; befintliga fält och source-filer bevaras. Saknade
eller kolliderande obligatoriska språk-/tasktokens avvisas och de tillagda
fältvärdena loggas i rapporten. Ingen filnamnsregel eller källtext ges.

## Vaani Hindi large-v3 – separat diagnostik

`ARTPARK-IISc/whisper-large-v3-vaani-hindi`, låst revision
`63b5cc7c542373350b6be1d8f8823291dce54b26`, Apache-2.0 enligt modellkortet:
https://huggingface.co/ARTPARK-IISc/whisper-large-v3-vaani-hindi.
Kortets base_model-metadata säger small, men config/processor anger
large-v3: 32 encoder-/decoderlager, d_model 1280, 128 melband.
Två safetensors-shards, sammanlagt **6 174 112 072 byte**. Matcha
utgivarens LFS-oid före inläsning (inte oberoende signatur):

- `model-00001-of-00002.safetensors`: 4 993 448 880 byte,
  `04e857b4797397f800424267cb0005704996f37a3991bd8d01ac335cfbf627f0`.
- `model-00002-of-00002.safetensors`: 1 180 663 192 byte,
  `282e507cf6dd88e8f692cae6d4268f10230dc65514c6e141792eff32ece91f74`.

Hämta index/config/processor/tokenizerfiler också, ingen remotekod.
Native HF-referens använder explicit `--weight-format safetensors`,
lokala shards, float32/CPU och samma övriga inställningar som ovan.
PyTorch-format är kvar som standard för äldre prov; ingen automatisk
osäker fallback. CT2-int8-konvertering separat/offline enligt ovan;
verifiera/generera lokal tokenizer.json före faster-whisper-provet.
Vaani CT2 4.8.2/int8 `model.bin` **1 558 949 857 byte**;
51 866 tokenizer-ID:n verifierade identiska, inga tokens behövde läggas
till. Härledd tokenizer.json SHA256
`5c1bf30c9e716e1477bedef846b01be0013daecb89e9e3ef7ab89b23c178df1b`.
Originalmix, sparad vokalstam och genererad tystnad ska ha identiska
hashar mellan motorerna. Ingen produktionsmodell ersätts av ett test.

Vasista22 original- och CT2-mappar raderades på användarens begäran
2026-09-29 efter avslutade underkända prov. Rapporter/testljud bevaras.
Instruktionerna ovan gör modellen möjlig att hämta/återskapa igen;
de innebär inte att Vasista-vikterna fortfarande finns lokalt.

## Oriserve Hindi2Hinglish Prime – separat diagnostik

`Oriserve/Whisper-Hindi2Hinglish-Prime`, låst revision
`4edf7b8291f950e3ac4f32bdacb38c46800e77dc`, Apache-2.0 enligt modellkortet:
https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Prime.
Utgivaren använder explicit **en/transcribe** i Transformers-exemplet
för latiniserad Hindi/Hinglish. Följ detta dokumenterade decoder-val
i Prime-provet; **en är här en instruktion till modellen, inte ett
bevis för engelskt källspråk eller svensk översättning**. Det skiljer
sig från tidigare hi-prov. Ingen förväntad replik eller translitterering
av modellsvaret ges som prompt. Befintliga språk-/kvalitetsspärrar i
produktion ändras INTE för att få ett Hinglish-svar godkänt som svenska.

Tretton lokala inferens-/processor-/tokenizer-/kortfiler, **6 180 022 556
byte** exklusive downloadmetadata. Exempelmedia och konverteringskod
från utgivaren hämtas inte. Två safetensors-shards **6 174 112 072 byte**,
SHA256 matchas mot publicerat LFS-oid (inte oberoende signatur):

- `model-00001-of-00002.safetensors`: 4 993 448 880 byte,
  `d5818bf9b2f8391fd7758c31373e6756f344c01690087fdb90bca0ace4ff3a42`.
- `model-00002-of-00002.safetensors`: 1 180 663 192 byte,
  `cc5b5f7e6931f295587ff772423e810edf976f2e625a652138d830b4423c3b97`.

Native `hf_whisper_probe.py --language en --weight-format safetensors`,
CPU float32/sex trådar/12 GB och i övrigt samma referensinställningar.
Samma sparade original-/vokal-WAV och fyra sekunder genererad nollsignal,
alla inputs skrivskyddade och inferens offline. Separat CT2-int8-
konvertering enligt ovan; tokenizer-ID kontrolleras strikt. Direkt
faster-whisper-prov använder explicit en, utan VAD/föregående text,
beam 5/temperature 0/ordtider. Språkfältet en i rapporten är TVINGAT,
inte automatisk identifiering. Rapporter aldrig produktionsundertexter.
Ingen kod från modellrepo exekveras och ingen safetensors-fallback till
osäker pickle. Modellkortet nämner också Apex; den hämtas/provas inte
automatiskt och ersätter inte den avtalade Prime-jämförelsen.

Oriserve CT2 4.8.2/int8 `model.bin` **1 558 949 857 byte**.
Alla 51 866 tokenizer-ID:n verifierade identiska utan tillägg;
härledd tokenizer.json har samma SHA256 som Vaani-tokenizern ovan.
Samma tokenizer/filstorlek betyder INTE att modellvikterna är samma.

HF-referensverktyget sparar nu `generated_token_ids` tillsammans med
text och antal token. Bara icke-negativa heltals-ID:n godtas; numeriska
NaN/Inf/flyttal avvisas före rapportsparande. Detta skiljer ett faktiskt
tokeniserat ord `nan` från ett numeriskt fel. Äldre rapporter utan råa
ID:n kompletteras vid behov med NY separat rapport, aldrig överskrivning.

## Extern ljudreferens – endast lokal diagnostik

`ai/reference_audio_probe.py` jämför korta, uttryckligen valda 0,5–10 s-
fönster i en tidigare hashverifierad 16 kHz mono-WAV-samling mot en
separat lagligt åtkommen 16 kHz mono-WAV-referens. Exempelargument:
`--reference /ref/preview-mono.wav --samples /samples --window 4:7
--window 13:16 --output /ref/NY_RAPPORT.json`. Reference/input/kod ska
monteras skrivskyddat, endast separat rapportkatalog skrivbar, nätverk av.
Verktyget letar efter högsta absolutvärde av normaliserad, samplexakt
FFT-korskorrelation över alla referensoffsets. Korrelationens tecken,
offset och målstart lagras; inga källord, modeller eller medienamn finns
i algoritmen. Icke-finit/tyst/ogiltigt ljud, fönster utanför ljudet och
överskrivning av rapport avvisas. Samma offset genom flera oberoende
fönster är starkare signalbevis än en ensam hög korrelation. Blandad
musik, filtrering, tempojustering och klippredigering kan sänka värdet:
en låg eller avvikande matchning BEVISAR INTE att något saknas i talet.
Ingen ASR-prompt, kvalitetsregel eller produktions-SRT påverkas.

För KING användes en offentlig 30,001 s Apple Music/iTunes-förhands-
lyssning av *Don 2*-spåret "Aa Raha Hoon Palat Ke" (track ID 1122289144,
35,107 s full spårlängd enligt uppslaget). Den sparas endast lokalt
under `data/diagnostics/king-don2-reference-20260929/` för analys,
ej i Git och inte för distribution. Publicerat spår:
https://music.apple.com/us/song/1122289144.
Jämför den inte med andra medier utan att först kontrollera licens,
utgåva och ljudets faktiska innehåll. Se senaste `STATUS.md` för
exakta hashvärden, träffar och avgränsning i KING-provet.

## Matchade ASR-fönster mot extern ljudreferens

`ai/aligned_asr_samples.py` skapar tre lika långa mono PCM16/16 kHz-prover
från ett befintligt hashverifierat original-/vokalmanifest och en separat
referens-WAV. Ange `--samples`, `--reference`, originalets `--start`/`--end`,
uppmätt `--reference-offset` och en NY `--output`-mapp. Exempel:
`--start 5 --end 20 --reference-offset 1.401 --output /results/aligned`.
Verktyget avvisar felaktigt format, ändrade källhashar, icke-ändliga tider,
fönster utanför ljudet och existerande output. Det kopierar endast utvalda
PCM-fönster till git-ignorerad diagnostik, aldrig över källfiler.

Det nya manifestet har varianterna `mono`, `vocals`, `reference`, lokal
tidsaxel 0 till fönsterlängden och egna SHA256-värden. Kör befintlig
`audio_channel_probe.py whisper` respektive `qwen` med samma `--samples`;
modellen laddas en gång per motor och behandlar alla tre i ordning. Behåll
källljud, referens, modeller och kod skrivskyddade; endast separat rapportmapp
skrivbar, `--network none`, inga produktionshemligheter eller Docker-socket.
Jämför publicerad text först EFTER transkribering. `reference` är en
diagnostisk variant, aldrig en produktionsingång eller automatisk facitkälla.
Kör `silence` separat för direkt hallucineringstest utan VAD. Se senaste
`STATUS.md` för konkreta KING-resultat och tydliga begränsningar.

## Talnärvaro över flera klipp – endast diagnostik

`ai/speech_presence_probe.py` läser ett valt `--media`-fönster med
`--start`/`--end` (högst 60 s) och skriver en NY `--output`-JSON.
Källan monteras skrivskyddat, rapporten i separat git-ignorerad katalog,
`--network none`. FFmpeg ger samma mono PCM16/16 kHz-format som worker-
fönstren. Befintlig Silero-VAD körs med worker-kodens strikta tröskel
0,72 och känsliga viskningströskel 0,15 samt deras respektive minimi-
och paddingvärden. Täckning är sammanslagen intervallängd, inte antal
verkligt yttrade sekunder; inga ASR-ord eller godkännanden skapas.

Jämför helst flera kända tal- och kontrollfönster. VAD-noll BEVISAR inte
tystnad: viskningar och ljudmix kan göra riktigt tal osynligt. Hög
känslig-VAD-täckning BEVISAR inte tal: musik kan reagera. En publik
referens eller tidigare undertext är bara jämförelseunderlag, inte facit
för nytt klipp. Något säkert instrumentalt realklipp var inte mänskligt
etiketterat i 2026-09-29-provet; generella musikregler kan därför inte
härledas från dess JSON-rapporter. Se senaste `STATUS.md`.
