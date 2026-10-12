# Ändringshistorik

Användarguiden finns i [README.md](README.md).

De äldre posterna nedan beskriver läget vid respektive version. Senare
rättelser gäller framför äldre slutsatser. Detta är inte en fullständig lista
över varje version; mellanliggande ändringar finns i Git-historiken.

## Utan versionshöjning (compose.yaml)

- `compose.yaml` (vanliga installationen): `subsynq`-tjänsten monterar `/movies`, `/tv` och `/extra` skrivbart så att fliken Städa undertexter kan flytta `.srt`-filer till papperskorgen. Workern och övriga tjänster är oförändrade. Containerns användare (`SUBSYNQ_UID`/`SUBSYNQ_GID`, standard 1000:1000) måste ha skrivrätt i mediamapparna.

## 0.8.221

- **Frivilligt lösenord och inloggningssida.** Under Inställningar → Lösenord kan du slå på ett lösenord (minst 8 tecken). Då visas en inloggningssida före allt annat; lösenordet sparas bara som en saltad PBKDF2-hash, sessionen är en signerad cookie (HttpOnly, SameSite, `Secure` över https) som gäller i 14 dagar, och efter fem felaktiga försök spärras klienten i fem minuter. Byte och borttagning kräver nuvarande lösenord, och att byta eller ta bort lösenordet avslutar alla sessioner. Av som standard, så befintliga installationer fungerar som förut.
- **Det som inte kräver inloggning:** `/health` (visar bara att tjänsten lever), inloggningssidan, ikonerna och webhooks (de har sin egen token). Alla övriga sidor och `/api/*` svarar med inloggningssidan respektive 401.
- **Cloudflare:** guiden (`unraid/README.md`) beskriver hur du når SubSynq via en Cloudflare Tunnel med Access som andra lås. Variabeln `SUBSYNQ_TRUST_PROXY=1` gör att spärren räknar per besökare bakom proxyn.

## 0.8.220

- **"Skapa undertext för markerade" frågar på sidan i stället för med en webbläsardialog.** Webbläsare kan blockera dialogrutor (`confirm`) för en webbplats utan att visa något, och då såg knapparna döda ut: ingenting hände och ingen förfrågan nådde servern. Bekräftelsen ("Köa N undertextjobb?" med Ja/Avbryt) visas nu som en rad i flervalsstapeln. Gäller alla tre flervalsknapparna (skapa, ta bort, skapa om).

## 0.8.219

Bättre hantering av tilltal, förlorade repliker och översättningsfel. Allt är allmänna regler som gäller alla filer; inget är knutet till en viss film, serie eller trailer.

- **Förlorade repliker och tilltal återställs bara med belägg.** Saknas ett tilltalsord, en mellanreplik eller en hel fras i ljudet kan SubSynq göra högst fyra korta, språkstyrda omlyssningar per fil. Kravet är två nya, förskjutna Whisper-läsningar och tidsatta ord från andra avlyssningen; befintliga rader, sluttider och grannar bevaras, och osäkra ord, konflikter och överlapp avvisas.
- **Sammanfogade, opunkterade dialogområden** får en begränsad omlyssning där Qwen först avgör språket. Ordtiderna jämförs mot faktiska ord, så att till exempel en engelsk sammandragning inte räknas som två överlappande ord.
- **Frasrättningen väljer sina citat ur den faktiska svenska texten.** Engelska källcitat kan inte väljas som ankare för en ändring. Hela flaggade repliker kan formuleras om från en separat betydelseförklaring; källtext, tider och andra rader skyddas, och en separat slutgranskning krävs fortfarande.
- **Jobb sparar sina avlyssningsprofiler** och råa ordtider före sammanslagningen (`/config/source-transcripts`) för felsökning.

## 0.8.218

- **Skapa om undertexten.** Rader som redan har en AI-undertext får knappen "Skapa om undertexten" (med bekräftelse), och det finns en flervalsknapp "Skapa om undertext för markerade (N)". Åtgärden använder den befintliga säkra omkörningen (den gamla AI-undertexten tas bort av workern, sedan körs filen om) och staplas aldrig på en fil som redan har ett jobb. Videofiler rörs inte.
- Förklaringstexten under de grå knapparna (0.8.217) säger nu bara att något pågår, eftersom filer med AI-undertext inte längre är en återvändsgränd.

## 0.8.217

- **Förklaring när "Skapa undertext för markerade" är grå.** Markerar du bara filer som redan har en AI-undertext (de kan bara tas bort) eller som har ett pågående jobb, visas nu en text som säger varför knappen inte går att trycka på, i stället för att ingenting händer.

## Utan versionshöjning (översättningsmotorns minne)

- **`translate-engine` får 16 GB och körs en begäran i taget** (`compose.yaml`, mallen `subsynq-translate-engine.xml`, guiden): `mem_limit` 16g och `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`. Med 12 GB kunde Gemma-motorn dödas av minnesbrist mitt i ett jobb (`HTTP 500 unexpected EOF`). Servern bör ha minst 32 GB RAM. Redan installerade behöver ändra containern: se `unraid/README.md`.

## 0.8.216

- **Ingen AMD-varning på en CPU-installation.** Texten "AMD Radeon RX 9070 XT svarade inte när jobbet startade" visades i gult under en körning även på servrar utan GPU-tjänst (till exempel Unraid med CPU). Den visas nu bara om en GPU-tjänst faktiskt är konfigurerad (AMD-tjänst eller NVIDIA CUDA).
- **Modellsidan laddar inte längre om sig själv.** Märket "ny version finns" använde samma stilklass som ett pågående jobb, vilket fick sidan att ladda om var tionde sekund och släcka ikryssade modeller. Märket har egen klass, och ikryssade modeller håller dessutom tillbaka automatiska omladdningar.
- **Webhook-adresserna visas som hela adresser.** Under Inställningar → Media visas Radarr-, Sonarr-, Jellyfin- och Emby-adressen nu med samma adress som sidan öppnades från (Unraids IP och port), med en tydlig anvisning att inte använda containernamnet `subsynq`.

## 0.8.213

- **Skanna ett bibliotek i taget.** Varje biblioteksflik (Filmer, Serier, Extra) har nu en egen skanningsknapp som bara läser den mappen (`/movies`, `/tv` eller `/extra`), och en separat knapp "Skanna allt". Resultatet från de andra biblioteken behålls, och sidan visar när varje mapp senast skannades.
- **Statuskort under skanning.** Överst på varje sida visas vilken mapp som läses och hur många filer som hittats hittills; sidan laddas om av sig själv när skanningen är klar eller har misslyckats. Skanningen körs i bakgrunden i stället för att låsa webbläsaren (`/api/scan-status`).
- **`/extra` är valfri.** Saknas mappen hoppas den över vid "Skanna allt" och visas som "inte ansluten"; skanningen misslyckas inte längre för att den saknas. Mallarna och guiden säger att raden kan tas bort.

## 0.8.212

- **Rättat vid nyinstallation på Unraid.** Workern väntar nu på att `subsynq` skapar databasen i stället för att krascha med `no such table: metadata` när den startas först. Installationsguiden (`unraid/README.md`) har ett nytt steg 1b: skapa mapparna och ge `models` ägare 99:100, annars får modellhämtningen `PermissionError` i `/models`.
- **Tydligt felmeddelande om modellmappen inte går att skriva i.** I stället för `PermissionError` visar Inställningar → Modeller vad som ska göras (`chown` på mappen som är kopplad till `/models`), och hämtningen provar igen av sig själv.

## 0.8.211

- **"Ny version finns" och uppdateringsknapp.** Modellhanteraren kontrollerar var 12:e timme (och på "Kontrollera nu") om de listade modellerna har en nyare version (Hugging Face: senaste commit; Gemma: registrets manifest jämfört med lokal digest). Kontrollen tittar bara. Under Inställningar → Modeller visas kolumnen Version, och för modeller med ny version ett formulär "Uppdatera markerade": den nya versionen hämtas och den gamla tas bort (Gemma: Ollama rensar gamla filer vid nästa omstart). Uppdateringen väntar tills inget undertextjobb körs och en misslyckad uppdatering lämnar den gamla versionen orörd. `POST /models/update`. 16 nya tester.
- Webbgränssnittets adress kan väljas (`SUBSYNQ_BIND_IP`, standard `127.0.0.1`).

## 0.8.210

- **Vilka modeller som används står nu i en egen fil, `models.json`** (ingen modelllista i koden). Filen följer med programmet (`app/models.json`); en egen kopia som `/config/models.json` har företräde. Workern hämtar Gemma-namnen för översättning och granskning därifrån (miljövariablerna `SUBSYNQ_TRANSLATION_MODEL`/`SUBSYNQ_REVIEW_MODEL` kan fortfarande åsidosätta men är borttagna ur compose-filerna). Ett felaktigt eget fil rapporteras tydligt och en annan giltig lista används.
- Ändras listan (ny eller bättre modell) hämtas den nya automatiskt. Den gamla ligger kvar tills du själv tar bort den under **Inställningar → Modeller**; bara modeller som SubSynq själv installerat (`/config/models-installed.json`) och som inte längre står i listan kan tas bort, och ingenting tas någonsin bort av sig självt. Ny sida `Inställningar → Modeller`, `POST /models/remove`. 16 nya tester.

## 0.8.209

- **AI-modellerna laddas ner automatiskt.** Workern startar en modellhanterare (`app/model_manager.py`) som vid varje start jämför disken med de sex modeller pipelinen använder (Whisper large-v3 och large-v2, Qwen3-ASR 1.7B, Qwen3-ForcedAligner 0.6B, Gemma 3 12B, Gemma 4 12B; ca 28 GB) och hämtar det som saknas: Whisper och Qwen från Hugging Face, Gemma via Ollama. Kontrollerar diskutrymme, försöker igen med växande väntetid, och skriver förloppet till `/config/models-status.json`.
- Lokala undertextjobb väntar tills alla modeller finns (raderingsjobb och molnkörning väntar inte). `SUBSYNQ_MODEL_AUTODOWNLOAD=0` stänger av nedladdning och väntan.
- Webbgränssnittet visar överst på varje sida ett fält med förlopp per modell medan något saknas (och ett tydligt fel om nedladdningen misslyckas); nytt API `/api/models`. 24 nya tester.

## 0.8.208

- Molnuppstart: väntan på serveradress visar nu leverantörens status samt om publik IP och tjänsteport finns. Vid adress-timeout hämtas Vasts containerlogg före städning och tas med i felbeskedet när den finns. Adresser, nycklar och godtycklig leverantörstext skrivs inte ut i statusraden. Tidsgränser, hyrval och AI-kedja oförändrade. Detta förbättrar diagnostiken; jobb27:s uppstartsorsak är fortfarande okänd.

## 0.8.207

- Översättningens befintliga omförsök bevarar rader som den oberoende granskningen uttryckligen godkänt. Bara underkända eller ogranskade rader översätts igen, med godkända grannrader som läskontext. Hela sammanslagna filen slutgranskas fortfarande; antal försök och kvalitetskrav oförändrade. Osäkra eller ändrade granskningsbelägg ger det tidigare hela omförsöket.

## 0.8.206

- ASR: återställer ett tappat inledningspronomen endast med giltiga v2-ordtider och separat Qwen-läsning som bekräftar hela den bevarade raden. Samma ord får Qwens skiljetecken/skiftläge; övriga ord, slut och grannar bevaras. Inga nya ord ur resten av Qwen-texten läggs till. Nytt verkligt helprov beskrivs i STATUS.
- Normaliserade kvarvarande lokala 600-rättigheter på i18n och tre testfiler till644 efter ett importfel i första 206-bygget; deras innehåll oförändrat.

## 0.8.205

- ASR: långa ordtider i det VAD-fria luckpasset kräver en annan matchande läsning före insättning eller gränsrättning. Hög ordsäkerhet ensam kan inte intyga ett ord som dragits över flera sekunders tystnad/musik. Vanlig lågmäld läsning bevaras; inga namn eller filmer hårdkodas.
- Översättning: den gemensamma betydelseregeln förtydligar att jämförelsernas båda led och konkreta substantiv ska bevaras även i opunkterade fragment. Faktiskt verifieringsresultat och kvarvarande fel anges i STATUS.

## 0.8.204

- Städa undertexter: efter "Återställ" skannas mapparna om automatiskt, så de återställda filerna syns direkt i listan utan manuell skanning. Nytt test som kör hela kedjan (flytta, återställ, töm) genom den riktiga servern mot temporära mappar (`tests/test_cleanup_routes.py`, 3 tester).

## 0.8.203

- Städa undertexter: nytt filter "Dubbletter" för videor med flera undertexter på samma språk (t.ex. `Film.sv.srt` + `Film.swe.srt` + `Film.ai.sv.srt`). Forced/SDH-varianter räknas som egna, SubSynqs `.ai`-tagg räknas inte som variant. Känner bara igen svenska och engelska. Grupperna sorteras ihop och märks "dubblett (en av N)". Filtret ignorerar typvalet och kombineras med de andra problemfiltren. Ny skanning behövs för äldre cache. 2 nya tester.

## 0.8.202

- Lokal motor: på GPU VRAM-raden står bara siffran (`7.4 / 24.0 GB`); läget (Arbetar/Ledig/Pausad) visas som en liten rad under stapeln, eftersom sidomenyn är smal. Utan mätning visas läget på raden som förut. Live-uppdateringen skapar läges-raden om siffran dyker upp efter sidladdningen. 3 nya tester (`tests/test_engine_card.py`).

## 0.8.201

- Engelska: Städa undertexter-fliken (filter, märken, tabeller, papperskorg, aviseringar, bekräftelser) och flervalsraden i biblioteket följer nu gränssnittsspråket (`i18n.py`, ~75 nya nycklar). Trasig-märkena lagras som koder (`empty`, `bad_encoding` …) och översätts vid visning; äldre cache med svensk text visas oförändrad. Övriga texter som fortfarande bara finns på svenska: felmeddelanden från servern (400/503), vissa inställningskort. 2 nya tester.

## 0.8.200

- Biblioteket: kryssruta per rad (när något går att göra), och en flervalsrad ovanför listan med "Markera alla som saknar undertext", "Markera alla med AI-undertext", "Avmarkera alla", "Skapa undertext för markerade" och "Ta bort AI-undertext för markerade" (båda med bekräftelse och antal). Ny route `POST /jobs/bulk` följer enkelknapparnas regler; filer som inte längre är aktuella eller redan har ett aktivt jobb hoppas över och räknas. Kryssrutorna hör till ett gemensamt formulär via `form=`-attributet (rader har egna formulär). Automatisk omladdning pausas medan något är ikryssat. 6 nya tester.

## 0.8.199

- Städa undertexter: nytt filter "Trasiga". Skanningen läser varje `.srt` (högst 400 kB) och märker filer som är tomma, saknar tidsatta repliker, bara har tomma repliker, har fel teckenkodning (inte UTF-8) eller trasiga tecken ("Ã¥"/"Ã¤"). Märket visas på raden. Filtret kombineras med "Videon bytt" (båda måste stämma) och ignorerar typvalet. 2 nya tester.

## 0.8.198

- Städa undertexter: papperskorgen kan tömmas automatiskt efter N dagar (0 = aldrig, standard). Inställningen sparas i `/config/cleanup-settings.json`; en bakgrundstråd kontrollerar var 6:e timme, och sidan kontrollerar när den öppnas. Kolumnen "Töms" visar datum per omgång. 1 nytt test.

## 0.8.197

- Lokal motor: GPU-raden heter alltid "GPU VRAM". När VRAM mäts visas läget efter siffran (`7.4 / 16.0 GB · Arbetar`, `· Ledig`, `· Pausad`); utan mätning visas bara läget som förut. Den live-uppdaterande JS:en behåller läget när siffran uppdateras.

## 0.8.196

- Sidomenyns "Lokal motor": GPU-raden heter nu "GPU VRAM" (visar använt/totalt grafikminne för hela kortet när GPU-tjänsten svarar).

## 0.8.195

- Städa undertexter: filtret sparas nu (`/config/cleanup-filters.json`) och används när sidan öppnas utan parametrar (menylänk, uppdatering, efter en flytt). Tidigare föll sidan tillbaka på "Andra undertexter" och visade 0 träffar trots sparad skanning.
- Knappen "Skanna om mapparna" skannar och omdirigerar till sidan utan `scan=1`, så en uppdatering (F5) skannar inte om. Tom träfflista föreslår "Alla" och anger hur många undertexter skanningen innehåller. 2 nya tester.

## 0.8.194

- Städa undertexter: nytt filter "Videon bytt sedan SubSynq skapade undertexten". Jämför videons storlek/ändringstid med fingeravtrycket som det färdiga jobbet sparade när det skrev `.ai`-undertexten (samma underlag som "Kör igen"). Gäller bara SubSynqs egna undertexter; andra undertexter har inget sådant avtryck. Märket "videon bytt" visas på raden. 1 nytt test.

## 0.8.193

- Ny flik **Städa undertexter** i sidomenyn under Översikt och Kö (`/cleanup`). Skannar `/movies`, `/tv` och `/extra` efter `.srt`, med filter för typ (andra/SubSynqs egna `.ai`/alla), saknar videofil, ålder och sökord, kryssrutor per rad, förhandsvisning och "flytta alla träffar".
- Inget raderas direkt: filer flyttas till `/config/trash/<omgång>/` med manifest och kan återställas per omgång eller tömmas. Bara riktiga `.srt`-filer under mediamapparna (inga symlänkar, aldrig video) kan flyttas. Ny modul `app/subtitle_cleanup.py`, 6 nya tester.

## 0.8.192

Frasrättningen återtar källans avsikt först och exakta avgränsade ändringar,
med granskarens föreslagna ordval fortsatt borttaget från modellindata. Varianten
som först beskrev måltexten och själv avgjorde `repair_needed` förkastades efter
191:s helprov: den bevarade ett idiomfel och skapade ett grammatikfel. Oberoende
slutkontroll, tidigare antal försök och alla ASR-/CPU-fixar behålls. Se STATUS
för faktisk verifiering; små rättningsprov är inget helgodkännande.

## 0.8.191

Lokal CPU avlastar Gemma med bekräftelse även mellan översättningsutkast och
dess kontrollpass, inklusive befintliga uppdelningar/omförsök. Varje anrop
behåller sin fullständiga kontext. Det riktiga Ray-provet i 190 sjönk till
0,17 token/s i utkastkontrollen och nådde 15 minuters timeout. GPU/moln,
översättningsregler, tidsgränser och antal försök ändras inte. Se STATUS för
mätningen av den nya versionen; timeouten är inget godkänt helprov.

## 0.8.190

Tilltalsåterställningen körs efter den befintliga slutliga gränssammanslagningen.
Helprovet i 0.8.189 visade att en tillfällig överlappning på 20 ms annars
stoppade ett belagt tilltal innan gränsen normaliserades. Överlappskontrollen
behålls; ändringen gäller ordningen på passen. Se STATUS för verifiering.

## 0.8.189

Ett kort tilltal som hörts i två v3-utsnitt och även av v2 kan återställas framför
en matchande namnreplik. Regeln kräver bekräftelse, giltiga ordtider, omedelbar
fortsättning och plats utan överlapp med grannrader. Ord och namn hämtas ur
avlyssningen; inget tilltal gissas fram. Detta hanterar enordsfragment som
luckåterställningens tidigare tvåordskrav tappade. Se STATUS för ljudproven.

## 0.8.188

När översättningens slutkontroll underkänner även det befintliga andra försöket
stoppas jobbet med källtexten och granskningsrapporterna kvar. Översättningsfel
byter inte längre hela avlyssningen till large-v2 på CPU eller moln. Verkliga
källfel kan fortfarande använda reservmodellen; molninstansen städas även vid
underkänd översättning.

Frasrättningen får felkategori och exakta citat utan granskarens föreslagna
formuleringar. Den beskriver både befintlig måltext och källans betydelse före
beslutet om en ändring behövs. Ett beslut att texten är korrekt får inte
innehålla ändringar. Oberoende slutkontroll och antal rättningsförsök behålls.
Se STATUS för verkliga prov och kvarstående fel.

## 0.8.187

Betydelserättningar med entydiga citat använder översättningsmodellen: först beskriver den källans
avsedda betydelse på engelska, sedan föreslår den exakta frasändringar på målspråket.
Granskningsmodellen gör den separata slutkontrollen. Metoden gäller även hela
korta idiom. Tvetydiga, överlappande eller felplacerade
ändringar avvisas. Grammatikpasset körs bara vid kvarvarande belagda grammatikfel
och får källtext som betydelsekontext. Det onödiga passet ändrade tidigare
"foton" till "fotoner" i Ray Gunn. Oberoende slutgranskning krävs fortfarande;
ingen extra rättningsrunda eller sänkt kvalitetsgräns införs. Se den interna statusloggen.

På lokal CPU avlastas modellen mellan slutgranskningens block så att sparade
promptar inte växer över flera block och konkurrerar med modellvikterna om RAM.
Varje block granskas fortfarande med samma kontext och kvalitetskrav. GPU och
molnkörningar behåller sin modellivscykel.

## 0.8.183

Ray Gunn jobb 25: spärren mot att kopiera granskarens ordalydelse stoppade ett
jobb trots att rättningen ("den här branschen") var rätt. Första kopian görs
fortfarande om en gång med frasen förbjuden, men en andra kopia godtas nu och
noteras i rapporten (`copied_reason_accepted`); den oberoende omgranskningen
avgör. Slutgrinden är oförändrad. Se den interna statusloggen.

## 0.8.182

Ray Gunn: när översättningens slutkontroll underkänner översättningen läses inte
längre hela ljudet om med den sämre Whisper large-v2 (det tappade "Get out!" och
"I like you" som large-v3 hörde). I stället görs översättningen om en gång med
källtexten kvar och de refuserade formuleringarna förbjudna; första försökets
rapport sparas som `job-N-attempt1.json`. Faller även andra försöket finns
v2-reserven kvar som sista utväg. Orsakstexten skiljer på källkontroll och
översättningens slutkontroll. Se den interna statusloggen.

## 0.8.181

Reservvägen (Whisper large-v2 efter underkänd large-v3): orsaken till att v3
underkändes sparas nu i jobbstatusen och i källfilen (`fallback_reason`), så att
den inte längre försvinner. Dessutom behåller `split_qwen_alignment` ett ord
vars ordtid är orimligt lång (forced aligner sträckte "I" över en tystnad) i
stället för att kasta det; tidigare försvann ordet ur Qwens rad. Ej bevisat
som orsak till molnets saknade "I" i Ray Gunn jobb 21 (inte återskapat
lokalt). Se den interna statusloggen.

## 0.8.180

Översättningens rättning och namn (Ray Gunn jobb 21): (A) en rättning får inte
innehålla en ordföljd på minst tre ord som bara står i granskarens motivering;
den görs då om en gång med frasen förbjuden, och kopierar den igen stoppas jobbet
("Håll med tittning" skrevs tidigare ut). (B) Ett ord som bara skiljer sig från
filens enda versala namnstavning med dubbelbokstav och som översättningen lämnat
oförändrat skrivs som filen skriver namnet ("gun" -> "Gunn"); vanliga ord och
filer som bara kopieras rörs inte. Allt rapporteras i kvalitetsrapporten. Se
den interna statusloggen för mätning och hur man går tillbaka.

## 0.8.179

Källavlyssning, två generella regler (Ray Gunn): (1) ett ensamt "I"/"you"/"we" …
före en rad som börjar med liten bokstav slås ihop med den raden innan filtren
för isolerade ord får ta bort det ("I like you, Gunn." i stället för "like you,
Gun."). (2) En lång rad utan någon meningsslutstecken ersätts av den korta
läsningens meningar när de alla (utom sista) är punkterade och säkra, även utan
hörbar paus; korta rop slås ihop ("Ray! Jump!"). Inga ord hittas på. Se den interna statusloggen
för mätning, begränsningar och hur man går tillbaka.

## 0.8.178

Källavlyssning: när två oberoende läsningar av samma tidsspann finns (90 s- och
30 s-fönstren, eller två överlappande fönster) vinner den fylligare om den
innehåller nästan alla ord i den andra och minst två ord till. Orsak: Ray Gunn
(jobb 18) tappade "Venus", hela meningar, "Stay tuned" och "I like you" trots att
precisionsläsningen hörde dem; den hoptryckta långfönsterläsningen behöll platsen.
Inga ord hittas på: allt kommer från en riktig läsning, osäkra rader (< 0,5)
räknas inte. Se den interna statusloggen för mätning, begränsningar och hur man går tillbaka.

## 0.8.177

Ord som filen själv skriver som namn (stor bokstav mitt i en mening eller efter
"Mr."/"Dr." m.fl., även över radgräns) räknas inte längre som oöversatta ord.
Orsak: Ray Gunn föll för att ljudigenkänningen skrev `gun` (låter som `Gunn`) och
granskaren flaggade det som oöversatt. Källtexten ändras aldrig; strykningen syns
i kvalitetsrapporten (`names_kept`), och översättaren får namnen som "kopiera
oförändrat". Alla andra spärrar är lika stränga som förut. Se den interna statusloggen för hur
man går tillbaka.

## 0.8.176

Översättningens slutkontroll: granskaren får en tydligare regel för citat redan i
frågan och, vid omförsök, exakt vilken regel som bröts. Rättningen får veta att en
anmärknings formulering inte är en godkänd översättning och vilken text som redan
avvisats. Spärrarna är oförändrat stränga. Moln-GPU på Vast har för första gången
kört hela kedjan (jobb föll i översättningens kvalitetskontroll, inte i molnet).

## 0.8.175

Moln-GPU hos Vast.ai väljer värd efter nätverkshastighet (minst 500, annars 300
Mbit/s nedladdning, tillförlitlighet 0,95, 80 GB disk) men behåller den GPU-modell
du valt (5060 Ti, 3090, 4090 ...). Kostar cirka 0,02 USD/h mer än den billigaste,
ofta långsamma värden. Ingen riktig körning är gjord.

## 0.8.174

Moln-uppstarten hämtar paket och modeller i fem parallella spår (och Qwen-modellerna
redan under uppstarten), visar varje spårs steg i hälsokontrollen och prövar de fyra
billigaste erbjudandena när en beställning avvisas. Lokalt provad (cirka 3,4 minuter
mot cirka 6 seriellt); på en riktig hyrd server är uppstartstiden fortfarande inte
mätt, och moln-GPU har ännu inte körts klart.

## 0.8.173

Hyrda GPU-servrar mäter nu grafikminnet per steg (Whisper, Qwen, Gemma) och
workern sparar toppvärdena per jobb, så att rätt GPU-storlek kan väljas efter
ett verkligt jobb i stället för en uppskattning. Ingen GPU-körning är ännu
genomförd, så inga mätvärden finns.

## 0.8.172

Lokala CPU-körningar låter Ollama läsa Gemma-vikterna som filcache (`use_mmap`)
i stället för att kopiera dem till vanligt minne. Mätt med identiska svar:
Gemma4 9,6 till 6,3 GiB och Gemma3 9,0 till 7,0 GiB ej frigörbart minne, så
servern har cirka 3 GiB mer tillgängligt medan en modell är laddad. Gäller bara
lokal CPU/RAM; moln och GPU påverkas inte. Hela jobb och långvarig drift under
minnestryck är inte verifierade.

## 0.8.171

Qwen på Unraid använder bfloat16 och en kortlivad process per förfrågan
för att begränsa och återlämna minne. Ett avgränsat ljudprov gick igenom tre
förfrågningar utan OOM; full film och flera språk återstår att verifiera.
694 tester passerade enligt senaste verifieringen. Gemma4 kan fortfarande
stoppas av värdens globala minnesbrist. Se den interna statusloggen för mätningar.

## 0.8.170

Översättning, slutgranskning och rättning ska bevara avsiktliga upprepningar,
orsak, riktning och betydelse. Fokuserad slutgranskning jämför källa och mål
för varje rad före omdömet. Verkligt dialogprov och negativprov verifierades;
680 tester passerade. Granskningen kan ta längre tid på CPU.

## 0.8.169

Whisper och Qwen balanserar korta slutfragment som skapats av tids-/teckengränser
vid riktiga ordtider. Översättning och granskning instrueras att använda
idiomatiskt målspråk. 676 tester passerade; befintliga SRT-filer skrevs inte om.

## 0.8.168

Lokala jobb visar processorns verkliga namn. Den oönskade hyresspärren från
0.8.166 är borttagen och translate-engine fick 16 GiB-tak. 671 tester
passerade. Den senare utredningen i 0.8.171 visar att ett högre containertak
inte löser värdens globala minnesbrist.

## 0.8.145

Videoinfo visar codec, upplösning, bildfrekvens, bitrate och HDR. Översikten
visar undertexttäckning per filmer, serier och extra, inklusive externa
undertextfiler. Ny skanning behövs för tidigare oanalyserade filer.
Verifiering av Windows-installationen finns i den interna statusloggen.

## 0.8.144

kan dela långa källrader utan att ändra ord när två oberoende tidsfönster och ljud-/textstöd bekräftar gränsen. Tidigare flaggade fragment får dessutom en explicit kontroll av faktisk sammanhängande måltext efter rättningen; riktiga fortsättningar mellan rader tillåts. KING:s sparade rad runt 00:16 delas nu, men hela svenska resultatet behöver fortfarande verifieras. HOPE:s källavlyssning är inte löst. Se STATUS för belägg och begränsningar.

## 0.8.143

avlastar Whisper före språkmodellernas arbete och återlämnar frigjort CPU-minne till systemet. En svår kort slutreplik får ett begränsat extra tidsprov utan sänkta ord-/kvalitetskrav. Nytt KING-ljudprov återfår slutrepliken och HOPE:s isolerade källgranskning passerar; nya hela körningar och korrekt svensk undertext återstår att verifiera. Se STATUS för exakta resultat och kvarstående osäkerheter.

## 0.8.142

sparar hårdvaruvalet direkt när listan ändras och skyddar valet mot automatiska sidomladdningar. Explicit CPU använder CPU för avlyssning och språkmodellsteg; lokal GPU får inte ta över och ett oläsbart hårdvaruval stoppar starten. Valet gäller nästa jobb, startar inget jobb och stoppar inte andra GPU-tjänster. KING:s tidigare långsamma slutgranskning återstår att felsöka.

## 0.8.141

förbättrar den generella slutkantskontrollen: en sammanfogad slutfras kan delas med exakt ordankare och bekräftade ordtider. Utdragna ord kräver ytterligare kort tidskontroll; inga kvalitetskrav sänks. Sparad KING-evidens ger en separat slutreplik, men ny helkörning och kontroll av svensk SRT återstår. **Kör om** från 0.8.140 finns direkt vid underkända/misslyckade körningar i kösidans **Senaste händelser** och håller dig kvar i kön. Dubbla/aktiva/föråldrade jobb stoppas; befintlig AI-undertext kräver bekräftelse före borttagning. Se den interna statusloggen för prov och begränsningar. Gemma 3 12B används för källgranskning/översättning och Gemma 4 12B för slutgranskning/riktad rättning. HOPE/KING har fortfarande osäker källtext; ett slutfört jobb är inte ett mänskligt godkännande av undertexten.

## 0.8.100

tar bort den gamla `/review`-granskningssidan (tidigt jämförelseverktyg mot en handfull manuellt valda avsnitt, se avsnittet "Granskning (borttagen)" nedan) och dess routes, mallar och `ai/build_*_review.py`-skript. Sidomenyns "Granskning" ersätts av en "Logg"-sida under Inställningar (`/settings/log`) som listar de senaste riktiga jobbhändelserna (start, klart, underkänt, fel) direkt från jobbtabellen. Se den interna statusloggen för detaljerad logg över versioner mellan 0.8.78 och denna.

## 0.8.78

lägger till tre nya färgteman: Y2K Cyber (holografiskt cyberrosa på djupt indigo), Vaporwave (pastellrosa och mint på lila natt) och Terminal (fosforgrön retro-CRT). Samma CSS-variabler och layout som de fem befintliga temana; bara nya färgvärden.

## 0.8.77

flyttar Inställningar-undersidorna (Allmänt, Tema, Moln-GPU) från en flikrad högst upp på sidan till egna, alltid synliga länkar i sidomenyn under en "Inställningar"-rubrik, i samma stil som Bibliotek-länkarna. Tema fick en egen sida (`/settings/theme`) separat från Allmänt (hårdvara + installation).

## 0.8.73

lägger till en kostnadsplan för moln-GPU under Inställningar. Leverantör (Vast.ai eller Runpod), GPU-modell, test/längre användning, egna priser för GPU, lagring och dataöverföring, valuta och planerad tid kan sparas och ändras manuellt. GUI:t visar en uppskattning av GPU plus angiven lagring. Molnvalen syns ännu som låsta i hårdvarulistan: ingen leverantör är ansluten och att spara planen hyr inget. För RTX 5060 Ti-provet och säker anslutning, se `CLOUD-GPU.md`.

## 0.8.72

rättar slutkontrollens felaktiga antagande att en svensk rad som börjar med liten bokstav är en avbruten mening. Sammanhängande tidsatta textblock granskas nu tillsammans, inklusive rader på båda sidor av blockgränsen; korta trailers får även en särskild granskning av slutet. Alla rader granskas, inte bara ett urval. Spärrarna mot borttappad översättning och obegriplig svenska finns kvar. Exakt svensk kandidat och granskningsbesked sparas lokalt i `data/config/translation-quality/`, även vid underkännande. Tidigare beskrivningar nedan av KING/PLANET 9:s underkännanden som bevisat korrekta var för kategoriska; se den interna statusloggen. Detta förbättrar kvalitetskontrollen, inte själva avlyssningen. Planen för framtida moln-GPU finns i `CLOUD-GPU.md`.

## 0.8.71

gör "Lokal motor"-kortet och "Pågående jobb"-kortet levande. CPU och RAM visar nu maskinens riktiga, aktuella användning (via `psutil` i AI-workern, skrivet till samma `hardware-status.json` som redan delades med webbgränssnittet) istället för "Kommer senare"; siffrorna uppdateras varje gång jobbet rapporterar framsteg, inte bara var tionde sekund i vila. Pågående jobb visar nu de fyra faktiska pipeline-stegen (Lyssnar och skriver av, Översätter till svenska, Kvalitetskontrollerar, Sparar undertext) med klar/pågår/väntar-status byggd direkt på jobbets riktiga status i databasen — inga påhittade steg, samma indikator används på Kö-sidan för alla aktiva jobb. Verifierat live: körde ett riktigt testjobb och såg CPU gå från 0 % (medan Whisper-modellen laddades) till 35 % och RAM från 1,5 till 3,8 GB under avlyssningen, med rätt pipeline-steg markerat som pågående.

## 0.8.70

gör Kö-sidan i gränssnittet färdig på riktigt: den listar nu alla aktiva jobb över hela biblioteket med förloppsstapel och avbryt-knapp, samt en historik över de senaste 20 avslutade/underkända/avbrutna jobben. Aktiva jobb kan sorteras efter senast uppdaterad eller status. Bygger enbart på data som redan fanns i jobbtabellen; inget nytt sparas. Kö och Granskning i sidomenyn var tidigare medvetna platshållare — Kö är nu en fullständig vy, Granskning är kvar som platshållare tills en generell per-fil-granskning med ljud och kandidatförslag byggs.

## 0.8.69

hittar och lagar den verkliga orsaken till att 0.8.68:s räddade tal ändå inte nådde fram: `split_word_cues()` i `app/asr_windows.py` slog ihop ord till en textrad och accepterade den bara om den *utfyllda* (paddade) tidslängden rymdes inom sjusekunderstaket. En ordgrupp vars faktiska ordlängd redan låg precis under taket kunde bli någon tiondels sekund för lång efter utfyllnaden och kastades då bort helt — inte förkortad, helt struken — trots att varje enskilt ord hade hög igenkänningssäkerhet. Utfyllnaden krymper nu istället för att hela raden stryks när den skulle bli för lång. Verifierat mot KING-trailerns riktiga ljud genom att köra hela avlyssningskedjan: en tidigare helt förlorad replik på cirka 7 sekunder (4,7–11,7s) finns nu med i slutresultatet. Översättningen av den utökade texten stoppas fortfarande av den svenska slutkontrollen, av samma skäl och på samma validerade sätt som HOPE — det är inte en bugg, utan kontrollen som gör sitt jobb på en genuint fragmentarisk källa. Generell fix, ett nytt test, ingen befintlig undertext eller mediefil berörd.

## 0.8.68

lagar en generell blind fläck i räddningslogiken för kort tal mellan repliker: den letade tidigare bara efter missat tal *mellan* två redan hittade rader, aldrig före den första eller efter den sista. För en hel avsnittsfil märks det knappt, men för en kort trailer där det enda upptäckta talet ligger mitt i klippet kunde flera sekunder verkligt tal vid början eller slutet aldrig ens övervägas för räddning. `plan_rescue_windows`, `plan_rescue_confirmations` och `fill_bounded_rescue_gaps` i `app/asr_windows.py` räknar nu klippets egen start och slut som en giltig granne när ingen tidigare eller senare rad finns. Verifierat mot KING-trailerns riktiga ljud: ett räddningsfönster och ett bekräftelsefönster skapades nu korrekt före den första repliken (tidigare noll fönster alls), och en oberoende omläsning återfann delar av det saknade talet. Den specifika biten förblev ändå för osäker för att tas med automatiskt (ingen bekräftelseomläsning nådde tillräcklig säkerhet) — vilket är rätt beteende, inte ett kvarvarande fel. Ingen befintlig undertext eller mediefil berörs; ändringen är generell och gäller alla filer, inte bara korta trailers.

## 0.8.67

sänker källkontrollens gräns för att känna igen en stabil icke-latinsk skrift från 30 till 20 bokstäver, eftersom en kort trailer ofta bara har några sekunders säkert avlyssnat tal. Verifierad tamilsk dialog visade sig redan vara trygg vid 20 bokstäver i testsviten; det är repetitionskontrollen, inte bokstavsgränsen, som stoppar en kort hallucinerad loop. En hindi-trailer med bara två korta repliker (KING) föll tidigare igenom gränsen och underkändes felaktigt som "icke-förståelig text" av den svensk/engelskspråkiga granskningsmodellen; ingen SRT skrevs och ingen mediefil påverkades. HOPE-trailerns senaste körning (jobb 76) visade sig vid närmare granskning innehålla minst en avhuggen källrad ("Es sind sind." → "De är.") trots att jobbet redovisades som klart; filen är kvar på disk men märks nu **Underkänd – använd inte** i väntan på ny körning eller manuell avlyssning. Inget annat i ASR- eller översättningspipen ändrades.

## 0.8.66

bygger om webbgränssnittet enligt en godkänd designspecifikation: en fast sidomeny (Översikt, Kö, Granskning, Bibliotek med Filmer/Serier/Extra, Inställningar) ersätter den tidigare flikraden. Översikt visar statkort per mediatyp, ett pågående jobb-kort och en kö-lista byggda på riktiga bibliotek- och jobbdata, aldrig påhittade siffror. Inställningar har fem valbara färgteman (Mörkt, Ljust, Midnatt, Skog, Bärnsten) och en fristående accentfärg som sparas per installation och gäller omedelbart. Hårdvaruvalet är oförändrat i sak men nås nu från både Översikt och Inställningar. Kö- och Granskning-sidorna är medvetet enkla platshållare i denna version; en fullständig könvy och en generell per-fil-granskning med ljud och kandidatförslag kräver mer bakomliggande arbete (jobbtabellen saknar ännu per-steg-status, och det finns ingen live CPU/RAM-mätning) och är inte byggda än. Inget i ASR- eller översättningspipen ändras av denna uppdatering, och ingen befintlig undertext eller mediafil berörs.

## 0.8.65

läser tydliga, fristående språkmarkeringar i filnamn, exempelvis `tyska`, `German` eller `Deutsch`, och låser då Whisper till rätt språk under hela avlyssningen. Motstridande automatisk skriftidentifiering får inte längre kringgå källkontrollen. Den svenska slutkontrollen gäller även korta trailers och stoppar flera avbrutna meningar, återkommande fristående meningsfragment eller när en orimligt stor del av källans dialog saknas. HOPE används som verkligt omprov; Sigma förblir underkänd utan skapad SRT tills tamil-avlyssningen blivit tillräckligt säker.

## 0.8.64

gör automatiskt ett slutligt Whisper-pass med fast språk när en dominerande skrift entydigt identifierar exempelvis tamil, telugu, malayalam eller koreanska. Sigma får därmed en tvingad tamilsk avläsning i stället för den trasiga automatiska språkblandningen. Efter översättningen bedöms dessutom hela svenska resultatet; genomgående translitterering eller oöversatt ordsallad stoppas innan en SRT skrivs.

## 0.8.63

låter en stabil, dominerande icke-latinsk skrift passera källkontrollen utan att en svensk/engelsk granskningsmodell felaktigt kallar ett obekant språk för nonsens. Om samma rad upprepas patologiskt används fortfarande den strikta kontrollen. Det gör att den tamilska Sigma-trailern kan översättas, medan Shrek-fallet fortfarande stoppas. Gränssnittet visar inte längre ett gammalt slutfört jobb som **AI-undertext skapad** när biblioteksskanningen visar att själva SRT-filen saknas.

## 0.8.62

kan läsa flera separata, mediespecifika uppsättningar verifierade källrättelser. Varje uppsättning är låst till exakt mediasökväg och filstorlek och avbryter säkert om den möter oväntad ASR-text. För FRANCE-trailerns två rader där både Whisper och Qwen hörde fel används den synliga engelska originalundertexten som verifierad reservkälla; en tredje avhuggen fransk rad återställs där två oberoende franska avläsningar är överens.

## 0.8.61

stoppar en körning före SRT-skrivning när en konservativ helhetskontroll bedömer att källtranskriberingen är genomgående söndertrasad, exempelvis av påhittat nonsens eller omfattande osammanhängande språkblandning. En separat andra översättningspass granskar dessutom varje svensk rad mot sin egen källrad och rättar tydliga betydelse- och grammatikfel. Underkända körningar visas som **Underkänd – använd inte** och kan startas om manuellt; befintliga undertexter skrivs aldrig över.

## 0.8.60

gör det möjligt att starta ett enskilt undertextjobb även från flikarna **Filmer** och **Övrigt**, förutsatt att filen finns i den aktuella biblioteksskanningen. Samma sökvägs- och överskrivningsskydd gäller som för serieavsnitt; inga jobb startas automatiskt.

## 0.8.59

gör filplaceringen flyttbar utan att media måste ligga inne i projektmappen. Serier, filmer och extra testmedia har separata värdsökvägar via `SUBSYNQ_TV_PATH`, `SUBSYNQ_MOVIES_PATH` och `SUBSYNQ_EXTRA_PATH`; Docker skapar inte tyst saknade mediamappar. Den lokala Windows-installationen flyttas till `E:\docker\SubSynq`, medan media läses från `E:\media\Serier`, `E:\media\Filmer` och `E:\media\Extra`. Modeller, databas, diagnostik och projektkod ligger samlat under SubSynq-mappen. Detta ändrar inte ASR- eller översättningsreglerna.

## 0.8.58

lägger till en begränsad, språkstyrd omlyssning av långa luckor där svagt tal finns men ingen undertextrad. Qwen3-ASR identifierar språket och ger en oberoende textavläsning; Whisper large-v3 gör ett kort pass med det språkvalet. En ny rad läggs till bara när texterna överensstämmer, ordsäkerheten är tillräcklig, svagt tal stöder tiden och ingen befintlig rad överlappas. Högst åtta misstänkta luckor per avsnitt får denna extra kontroll, högst tre per kvart, och en redan bekräftad rad behöver inte ett andra Whisper-pass. Det är inte ett löfte om korrekt text på alla språk: språkvalet omfattar de Qwen-språk som SubSynq kan mappa till Whisper, däribland franska, japanska och kinesiska. Ett fokuserat Daryl S01E01-prov återfann `Tu es le remède d'un monde malade` vid 52:22–52:26. Yukon S01E01:s första 90 sekunder var oförändrade rad för rad, och 124 automatiska tester passerade. Ett helt nytt avsnitt eller faktiskt japanskt/kinesiskt material har ännu inte verifierats. Inga äldre SRT-filer skrivs om automatiskt.

## 0.8.57

är en diagnostik- och säkerhetskopieringsuppdatering, inte en ny automatisk ASR-regel. Projektets kod är nu versionshanterad och uppladdad till det angivna GitHub-arkivet; `data/`, media, undertexter, modeller och `.env` hålls utanför. Nya korta provverktyg kan jämföra mono/center, talfilter av/på och uttrycklig språkledtråd. Daryl S01E01 vid 52 minuter visade att kort fransk avläsning utan VAD fångar repliker som jobb 36 missade. Resultaten och försiktighetskrav för nästa generella luckräddning finns i `SPRAKLUCKOR-S01E01.md`. Inga befintliga undertexter skrevs om och ingen helkörning startades.

## 0.8.56

låter en vanlig körning fortsätta med Whispers tidsatta text när Qwens valfria kontroll ger enbart ord med oanvändbara tidskoder. Det osäkra Qwen-fönstret får inte ändra text eller tider; gränssnittet visar att kontrollen hoppades över och slutstatus anger antal och första tidpunkt. Ett test med ett nollångt Qwen-ord och sammanlagt 117 tester passerade. Gemma 4 12B installerades enbart för jämförelse med Gemma 3 12B på samma 42 befintliga engelska/franska rader. Båda bevarade ordningen och alla rad-ID:n; Gemma 4 förbättrade några fraser men gav också tydliga felöversättningar, bland annat `dépouiller` → `rulla oss` i stället för att råna. Gemma 3 är fortsatt produktionsvalet. Ingen helkörning startades automatiskt.

## 0.8.55

lägger till en avgränsad kontroll av korta fraser där Whispers två avläsningar hör samma början men olika slut. Qwen får då en uttrycklig engelsk språkledtråd; det ursprungliga ordvalet ersätts bara om Whisper v2 oberoende hör exakt samma nya fras på samma tid och taldetektorn stöder ljudet. I ett nytt riktigt ljudprov från Daryl S01E01 vid 16:30 blev resultatet `Sit down he said` på 990,74–991,82 sekunder, medan de franska replikerna före förblev separata. Qwen och v2 hörde samma engelska fras; Qwen med fransk omgivning hade tidigare hört `il sait`. Detta är en generell spärr mot språkbytesfel med engelska, inte en hårdkodad ändring av avsnittet. Andra språkledtrådar kan läggas till senare. 116 tester passerade. Ingen helkörning eller befintlig SRT har skrivits om.

## 0.8.54

använder Qwen3-ASR 1.7B på CPU enbart för korta, isolerade enordsrepliker där två Whisper-avläsningar är överens om ett osäkert ord men ljuddetektorn anger en gränsförskjutning. Varje kandidat får två korta, olika förskjutna kontrollfönster (högst 24 kandidater per avsnitt); Qwen får lägga till ett enstaka ord bara när båda fönstren och separat taldetektion stöder samma tid. För 5.1/7.1-ljud läses centerkanalen i kontrollen; andra ljudformat läses som mono. Modellen laddas vid behov och frigörs uttryckligen före översättning. På Daryl S01E01:s korta 17:38-prov gav Qwen tidsatt `Merci` vid 17:38, Whisper v3 på centerkanalen `Thank you` och taldetektorn stöd vid samma tid. 16:30 är ännu omstritt (`il sait`/`ici`/`he said`) och korrigeras inte automatiskt. Ingen helkörning eller befintlig SRT har ändrats.

## 0.8.53

stoppar översättningsblock där Gemma kopierar engelsk eller fransk källtext rakt in i den svenska undertexten; sådana block görs om i mindre delar och en enskild rad får tre försök innan jobbet stoppas öppet. Ett verkligt franskt prov från Daryl S01E01 översattes korrekt när det skickades ensamt. En ny, snäv ASR-regel väljer också den fras som två separata v3-avläsningar hörde vid 25:51–25:53 (`You ought to get some air.`) framför det motsägande `get somewhere. Of`; återspelning av det sparade diagnosspåret bekräftade att spökordet försvann. 17:38 är fortfarande osäkert: v2 gissade `Merci` med mycket låg säkerhet under musik, så ingen text läggs in där automatiskt. 16:30:s felaktiga två ord återstår också. Ingen befintlig SRT har skrivits om.

## 0.8.52

avvisar nu tidsersättningar från både Whispers korta v3-pass och selektiva v2-pass om de skulle överlappa en annan undertextrad, även med några hundradels sekunder. Tidigare kunde en tolerans på 0,1 sekund eller ett utbyte av meningsgränser släppa igenom ett överlapp, vilket stoppade hela körningen. Felmeddelandet visar nu även var ett eventuellt nytt överlapp finns. Detta ändrar inte ASR- eller översättningsmodellerna och startar ingen ny helkörning automatiskt.

## 0.8.51

använder Whisper large-v2 på CPU som en selektiv andra avläsning av högst åtta korta, osäkra v3-ordfragment och högst fyra tydligt förskjutna enordsrepliker per avsnitt. V2 laddas bara när sådana fönster finns och frigörs direkt efteråt. V2 får bara ersätta ett fragment om v3:s kortare avläsning också hör det saknade längre ordet; ett tidsförskjutet enordsord kräver dessutom stöd av v3:s räddningspass och v2 på den senare tiden. En ensam Whisper-profil får inte längre bekräfta sin egen korta spökrad. På Daryl S01E01:s verkliga fönster blev 14:20 `My, uh, grandfather.` i stället för ett ensamt `father`, och 31:30-provet tappade spökordet `Do` och fick `No.` vid cirka 31:40. Vid 16:31 avstod v2-kontrollen från ändring eftersom avläsningarna var oense om `he said`/`you say`; det ordfelet återstår. Tidigare repliker vid 04:36 och 05:02 behölls. 106 tester passerade. Ingen helkörning startades och befintlig SRT ändrades inte.

## 0.8.50

väljer den kortare Whisper-avläsningens tydligare meningsgränser när båda avläsningarna täcker samma ljud och i huvudsak är överens om orden. Översättaren får nu uttrycklig instruktion att aldrig flytta eller upprepa ord mellan rad-ID:n. På S01E01:s faktiska helavsnittsfönster delades 05:40–06:04 upp i hörbara repliker i stället för för tidiga, avhuggna rader; ett avgränsat svenskt prov höll bland annat Daryl, Commonwealth och Amerika på rätt tider. 04:36 och 05:02 behölls i återspelning, och 101 tester passerade. Kvarstående fel vid 14:20, 16:31 och omkring 31:30 är bekräftade i råavläsningen: Whisper large-v2 gav bättre ord i korta diagnostikprov men är inte aktiverad i vanliga jobb. Ingen ny helkörning eller SRT-ändring gjordes. CPU, Whisper large-v3 och Gemma 3 12B är fortsatt produktionsvalet.

## 0.8.49

återställer en bekräftad replik efter en längre paus och låter en förskjuten avläsning ersätta ett ofullständigt, överlappande fragment bara när orden stämmer. En kort enordsrad utan stöd i vare sig taldetektering eller andra avläsningen tas bort. Kontroller mot originalfilens faktiska helavsnittsfönster återgav `Sue had a heart attack.` vid 04:36 och hela `She wants things the way they were.` vid 05:00–05:03; den obekräftade `I'm` vid 13:53 försvann. Namnet Sue är fortsatt värt manuell avlyssning eftersom Whisper var osäkert om första ordet, även om ett separat Qwen-prov också hörde Sue. 97 tester passerade. Ingen befintlig SRT skrevs över; CPU och nuvarande hårdvaruval ändrades inte.

## 0.8.48

gör en andra, förskjuten kortavläsning av högst 12 osäkra repliker per avsnitt när den första no-VAD-avläsningen hittar en längre fras mellan verkliga dialograder men inte uppfyller den vanliga, stränga kvalitetsspärren. En sådan fras accepteras endast om de två avläsningarna överensstämmer i ord och tid. Det tidigare gränsfallet i S01E01 03:40–05:10 får nu med `she wants things the way they were` 04:59,93–05:03,17; provet 04:40–06:10 behåller både den hela meningen och den bättre tidssättningen vid 05:40. Tidigare spökrader återkom inte, och den franska kontrollscenen var oförändrad. 93 tester passerade. Den gamla E01-filen på mediadisken har döpts om till `.ai.sv.srt.backup-0.8.45`, så en ny vanlig `.ai.sv.srt` kan skapas senare; ingen ny helkörning startades automatiskt. CPU är fortsatt vald och AMD/Qwen är pausade i vanliga jobb.

## 0.8.47

låter en kort, avgränsad Whisper-omläsning komplettera en ofullständig replik när ordens början eller slut tydligt matchar den befintliga raden. När lång- och kortpasset säger nästan exakt samma ord men kortpasset hittar en tydlig paus används den senare, snävare tidsdelningen. I ett 90-sekundersprov på S01E01 återkom `She wants things the way they were.` vid 05:00,17–05:03,19, och `it's in america` flyttades från en för tidig sammanfogad rad till 05:44,58–05:46,02. 91 tester passerade och kortprov på tidigare spöktext och fransk dialog kördes. Ett prov med annan fönstergräns missar fortfarande delar vid 05:03; detta är ett begränsat lyft, inte en garanti för alla avsnitt. Den befintliga E01-SRT:n lämnas orörd. CPU är fortsatt vald och Qwen/AMD är inte aktiva i vanliga jobb.

## 0.8.46

dokumenterar ett avgränsat CPU-prov på rätt S01E01 vid 05:03 och 05:40. Whisper large-v2, large-v3 och Qwen3-ASR 1.7B hittar den saknade frasen när hela kortklippet läses, men båda Whisper-versionerna tappar den med VAD på. Korta Whisper-avläsningar sätter även replikerna kring 05:40 betydligt snävare än den nuvarande SRT-raden. Slutsatser och råresultat finns i ASR-FOKUS-S01E01-0503-0540.md (äldre referens: ASR-FOKUS-S01E01-0503-0540.md). Den aktiva modellen är fortfarande Whisper large-v3 på CPU; ingen befintlig SRT skrivs om av denna uppdatering.

## 0.8.45

innehåller den färdiga nya E01-undertexten från jobb 26 (503 repliker). `Maybe` och det fristående `Safe` vid 04:03/04:17 är borta; den bekräftade engelska repliken visas 04:07,86–04:11,08 och den franska hälsningen finns vid 13:46. En manuellt språkgranskad svensk rad ersätter modellens onaturliga ”Vi kretsade runt Spanien”. Föregående E01-SRT och den nygenererade filen före språkgranskningen är bevarade under `data/config/review/backups`. Resultatet bör fortfarande avlyssnas som helhet innan det godkänns.

## 0.8.44

använder avlyssnade källrättelser i en ny E01-körning. De sex avgränsade ändringarna gäller endast exakt samma videofil och kontrolleras mot den nya råavläsningen innan översättning; vid oväntad skillnad stoppas jobbet tydligt. Den råa ASR-texten och den text som faktiskt översätts sparas separat per jobb. Detta är en manuell, verifierad korrigering för E01 – inte ett påstående om att alla luckor i andra avsnitt nu upptäcks automatiskt.

## 0.8.43

sparar din avlyssningsåterkoppling i [E01-fokusprovet](http://localhost:8765/review/focus): `Circled Spain, nowhere safe.` är bekräftad vid cirka 04:08, och `Maybe` samt det fristående `Safe` är spökrader i samma prov. Den franska scenen vid 13:45–13:55 verkar nu ligga rätt enligt din avlyssning; ordalydelsen stöds av korta Whisper-kontroller men den svenska översättningen behöver fortfarande granskas. Befintlig SRT ändras inte av denna märkning.

## 0.8.42

har en separat [avlyssning av aktuellt E01-fokusprov](http://localhost:8765/review/focus) vid cirka 04:08 och 13:47. Den visar vad korta, riktade Whisper-avläsningar återfann jämfört med jobbets sparade källtext. Detta är inte en ny komplett undertext och den befintliga SRT-filen ändras inte. En begränsad planering av misstänkta luckor finns för fortsatt diagnostik men körs ännu inte i vanliga jobb. CPU är fortsatt valt; AMD/ROCm och Qwen-ASR är inte aktiva i produktion.

## 0.8.41

använder fortsatt uttryckligen CPU medan AMD/ROCm är pausad. Qwen3-ASR 1.7B och ForcedAligner 0.6B finns som avgränsat diagnostikprov, men är inte aktiva i vanliga undertextjobb. Korta talöar gav bättre tider för flera repliker, medan taldetekteringen missade 04:08 och Qwen fortfarande gav nollängdsord och skiftande engelsk text vid samma klipp. En ny kvalitetsspärr stoppar därför osäkra Qwen-tider innan de kan bli undertextrader. Sidan för avlyssningsprov är tydligt märkt som ett arkiv med äldre utvecklingsutdrag. Varje befintlig `.ai.sv.srt` kan tas bort säkert från biblioteksvyn efter en bekräftelse. Whisper large-v3 har fortsatt separat taldetektering och den oöversatta originaltranskriberingen sparas per jobb i `/config/source-transcripts`.
