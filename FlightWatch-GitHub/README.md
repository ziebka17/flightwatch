# FlightWatch – wyszukiwarka zbugowanych cen lotów z Gdańska (GDN) do Ameryk i Azji

Program skanuje ceny biletów powrotnych (z przesiadkami) z Gdańska do 23 dużych miast,
zapisuje każdą zaobserwowaną cenę i wysyła Ci maila, gdy cena jest **poniżej ustalonego progu**
**lub** znacznie tańsza niż zwykle na tej trasie. Obie reguły działają jednocześnie.

## Skąd bierze ceny (przeczytaj najpierw)

Stron linii lotniczych nie da się niezawodnie scrapować (blokują boty i ciągle zmieniają układ),
a Amadeus zamknął swój darmowy portal dla programistów 17 lipca 2026. Program korzysta więc z
**Travelpayouts / Aviasales Data API** – darmowego, bez rozmów handlowych, z natychmiastową rejestracją.
Zwraca najtańsze ceny znalezione przez wyszukiwarkę Aviasales (setki linii i pośredników, w tym
połączenia z przesiadkami). Dane pochodzą z pamięci podręcznej wyszukiwań, więc zawsze potwierdź
cenę linkiem z maila przed zakupem.

## Uruchomienie na Macu (najprościej)

1. Upewnij się, że w pliku `.env` jest Twój token Travelpayouts (jeśli nie: skopiuj go
   z https://app.travelpayouts.com/profile/api-token).
2. Utwórz **hasło aplikacji** Gmail: https://myaccount.google.com/apppasswords
   (wymaga włączonej weryfikacji dwuetapowej). Zwykłe hasło do Gmaila nie zadziała.
3. Kliknij dwukrotnie **„Uruchom FlightWatch.command”**. Przy pierwszym uruchomieniu skrypt
   poprosi o hasło aplikacji, wyśle testowego maila i zacznie skanować co 6 godzin.
   Zostaw okno Terminala otwarte – zamknięcie zatrzymuje program.
   Jeśli macOS zapyta o instalację „narzędzi programistycznych” – zgódź się (to Python)
   i uruchom plik ponownie.
4. **„Test bez maila.command”** – jedno skanowanie z wynikiem na ekranie, bez wysyłania maila.

Jeśli macOS nie pozwala otworzyć pliku `.command` („nie można zweryfikować dewelopera”):
kliknij go prawym przyciskiem → „Otwórz” → „Otwórz”. Gdyby pojawiło się „Permission denied”,
otwórz Terminal i wpisz: `chmod +x ~/Documents/FlightWatch/*.command` (dostosuj ścieżkę).

## Uruchomienie ręczne (Windows / Linux / serwer)

Wymagany jest tylko Python 3.10+ (bez dodatkowych bibliotek). W folderze programu:
`python -m flightwatch.main --test-email`, potem `python -m flightwatch.main --loop 6`.

## Dostrajanie `config.json` (otwórz w TextEdit)

* `regions.*.fixed_threshold` – cena biletu powrotnego (PLN), poniżej której zawsze chcesz alert.
  Domyślnie: 1200 zł Ameryki, 1000 zł Azja. Realne błędne taryfy to często 600–1500 zł.
* `history.drop_fraction: 0.45` – alert, gdy cena jest o 45 %+ niższa od mediany na tej trasie
  z ostatnich `lookback_days` dni. Potrzebuje najpierw `min_samples` (8) wcześniejszych cen,
  więc reguła historyczna zaczyna działać po kilku uruchomieniach.
* `search.months_ahead` (6) – ile miesięcy w przód; `trip_length_min_days` / `max_days` (5–21) –
  akceptowana długość wyjazdu. Jedno zapytanie pokrywa cały miesiąc, więc skanowanie to
  23 miasta × 6 miesięcy = **138 zapytań** (API jest darmowe, limit 600 zapytań/min).
* `search.max_alerts_per_destination` (3) – żeby jeden tani miesiąc nie wygenerował 30 maili.
* `api_budget.monthly_max_calls` – twardy limit, zabezpieczenie przed zapętleniem.
* Miasta możesz dowolnie dodawać i usuwać; używaj kodów miast IATA (NYC, TYO, SAO obejmują
  wszystkie lotniska danego miasta).

## Polecenia

| Polecenie | Co robi |
|---|---|
| `python -m flightwatch.main` | jedno skanowanie, wysyła alerty mailem |
| `python -m flightwatch.main --loop 6` | skanuje co 6 h bez końca |
| `python -m flightwatch.main --dry-run` | skanuje i wypisuje, bez maila, nic nie oznacza jako wysłane |
| `python -m flightwatch.main --report` | najtańsza cena na każdej trasie z ostatnich 24 h |
| `python -m flightwatch.main --test-email` | wysyła testowego maila |
| `python -m pytest` | uruchamia 17 testów jednostkowych |

## Jak wygląda alert

```
GDN -> NYC  2026-11-03 .. 2026-11-10  890 PLN
   tam:  GDN → JFK (LO, 1 przes.)  (1 przesiadka/-ki)
   pow.: JFK → GDN (LO, 1 przes.)  (1 przesiadka/-ki)
   dlaczego: poniżej progu 1 200 PLN; 58% taniej niż zwykle (~2 100 PLN)
   sprawdź: https://www.aviasales.com/search/...
```

Ta sama trasa/daty/cena nie jest wysyłana ponownie przez `alerts.cooldown_hours` (24 h).

## Zabezpieczenia

* Ceny porównywane są tylko w skonfigurowanej walucie; inne są pomijane, a nie błędnie porównywane.
* Oferta jest sprawdzana z historią **przed** zapisaniem, więc tania cena nigdy nie obniża
  własnego punktu odniesienia.
* Błędy sieci, limity (429) i błędy serwera są ponawiane; awaria maila nie zatrzymuje pętli –
  alert zostanie ponowiony przy następnym skanowaniu.
* Błędne taryfy znikają w ciągu godzin. Zawsze potwierdź cenę na stronie linii/pośrednika przed
  zakupem.
