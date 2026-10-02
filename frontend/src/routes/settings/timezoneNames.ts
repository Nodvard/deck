/**
 * Deutsche Namen für die gängigen Zeitzonen, damit die Suche auch mit „Wien“, „Zürich“ oder
 * „Großbritannien“ etwas findet (die Zonen heißen in der Liste des Browsers englisch:
 * `Europe/Vienna`). Der erste Eintrag ist die Beschriftung in der Liste, die übrigen sind weitere
 * Suchbegriffe (Städte, Länder, die Zeit-Namen wie „Mitteleuropäische Zeit“). Was hier fehlt, bleibt
 * über den englischen Namen auffindbar.
 */

const MEZ = "Mitteleuropa, Mitteleuropäische Zeit, MEZ, MESZ, CET";
const WEZ = "Westeuropa, Westeuropäische Zeit, WEZ";
const OEZ = "Osteuropa, Osteuropäische Zeit, OEZ, EET";

/** Zone -> „Beschriftung, Suchbegriff, Suchbegriff …“. */
const RAW: Record<string, string> = {
  UTC: "Weltzeit, Koordinierte Weltzeit, UTC, GMT, Greenwich",
  // Mitteleuropa
  "Europe/Berlin": `Deutschland, Berlin, Hamburg, München, Köln, Frankfurt, Stuttgart, ${MEZ}`,
  "Europe/Vienna": `Österreich, Wien, Graz, Salzburg, Linz, Innsbruck, ${MEZ}`,
  "Europe/Zurich": `Schweiz, Zürich, Bern, Basel, Genf, Lausanne, ${MEZ}`,
  "Europe/Vaduz": `Liechtenstein, Vaduz, ${MEZ}`,
  "Europe/Luxembourg": `Luxemburg, ${MEZ}`,
  "Europe/Amsterdam": `Niederlande, Amsterdam, Holland, Rotterdam, ${MEZ}`,
  "Europe/Brussels": `Belgien, Brüssel, Antwerpen, ${MEZ}`,
  "Europe/Paris": `Frankreich, Paris, Lyon, Marseille, ${MEZ}`,
  "Europe/Madrid": `Spanien, Madrid, Barcelona, ${MEZ}`,
  "Europe/Rome": `Italien, Rom, Mailand, Neapel, Turin, ${MEZ}`,
  "Europe/Copenhagen": `Dänemark, Kopenhagen, ${MEZ}`,
  "Europe/Stockholm": `Schweden, Stockholm, ${MEZ}`,
  "Europe/Oslo": `Norwegen, Oslo, ${MEZ}`,
  "Europe/Warsaw": `Polen, Warschau, Krakau, ${MEZ}`,
  "Europe/Prague": `Tschechien, Prag, ${MEZ}`,
  "Europe/Bratislava": `Slowakei, Pressburg, ${MEZ}`,
  "Europe/Budapest": `Ungarn, Budapest, ${MEZ}`,
  "Europe/Ljubljana": `Slowenien, Laibach, ${MEZ}`,
  "Europe/Zagreb": `Kroatien, Agram, ${MEZ}`,
  "Europe/Belgrade": `Serbien, Belgrad, ${MEZ}`,
  "Europe/Sarajevo": `Bosnien und Herzegowina, ${MEZ}`,
  "Europe/Skopje": `Nordmazedonien, ${MEZ}`,
  "Europe/Tirane": `Albanien, Tirana, ${MEZ}`,
  "Europe/Malta": `Malta, Valletta, ${MEZ}`,
  "Europe/Monaco": `Monaco, ${MEZ}`,
  "Europe/Andorra": `Andorra, ${MEZ}`,
  "Europe/Vatican": `Vatikanstadt, Vatikan, ${MEZ}`,
  "Europe/San_Marino": `San Marino, ${MEZ}`,
  "Europe/Gibraltar": `Gibraltar, ${MEZ}`,
  // Westeuropa
  "Europe/London": `Großbritannien, London, Vereinigtes Königreich, England, Schottland, Wales, UK, ${WEZ}`,
  "Europe/Dublin": `Irland, Dublin, ${WEZ}`,
  "Europe/Lisbon": `Portugal, Lissabon, ${WEZ}`,
  "Atlantic/Reykjavik": `Island, Reykjavik, ${WEZ}`,
  "Atlantic/Canary": `Kanarische Inseln, Kanaren, Teneriffa, ${WEZ}`,
  "Atlantic/Azores": "Azoren, Portugal",
  // Osteuropa
  "Europe/Athens": `Griechenland, Athen, ${OEZ}`,
  "Europe/Helsinki": `Finnland, Helsinki, ${OEZ}`,
  "Europe/Tallinn": `Estland, ${OEZ}`,
  "Europe/Riga": `Lettland, ${OEZ}`,
  "Europe/Vilnius": `Litauen, Wilna, ${OEZ}`,
  "Europe/Kyiv": `Ukraine, Kiew, ${OEZ}`,
  "Europe/Kiev": `Ukraine, Kiew, ${OEZ}`,
  "Europe/Bucharest": `Rumänien, Bukarest, ${OEZ}`,
  "Europe/Sofia": `Bulgarien, Sofia, ${OEZ}`,
  "Europe/Chisinau": `Moldau, Moldawien, ${OEZ}`,
  "Asia/Nicosia": `Zypern, Nikosia, ${OEZ}`,
  "Europe/Istanbul": "Türkei, Istanbul, Ankara",
  "Europe/Moscow": "Russland, Moskau, Moskauer Zeit, MSK",
  "Europe/Minsk": "Belarus, Weißrussland, Minsk",
  "Europe/Kaliningrad": `Russland, Kaliningrad, Königsberg, ${OEZ}`,
  // Nordamerika
  "America/New_York": "USA Ostküste, New York, Washington, Boston, Miami, Atlanta, Östliche Zeit, Eastern, EST",
  "America/Chicago": "USA Mitte, Chicago, Texas, Houston, Dallas, Zentrale Zeit, Central, CST",
  "America/Denver": "USA Gebirge, Denver, Salt Lake City, Rocky Mountains, Mountain, MST",
  "America/Phoenix": "USA, Arizona, Phoenix, Mountain",
  "America/Los_Angeles": "USA Westküste, Los Angeles, Kalifornien, San Francisco, Seattle, Las Vegas, Pazifische Zeit, Pacific, PST",
  "America/Anchorage": "USA, Alaska, Anchorage",
  "Pacific/Honolulu": "USA, Hawaii, Honolulu",
  "America/Toronto": "Kanada Ost, Toronto, Ottawa, Montreal",
  "America/Vancouver": "Kanada West, Vancouver",
  "America/Edmonton": "Kanada, Edmonton, Calgary",
  "America/Winnipeg": "Kanada, Winnipeg",
  "America/Halifax": "Kanada, Halifax, Atlantikküste",
  "America/St_Johns": "Kanada, Neufundland, St. John's",
  "America/Mexico_City": "Mexiko, Mexiko-Stadt",
  "America/Havana": "Kuba, Havanna",
  "America/Panama": "Panama",
  "America/Bogota": "Kolumbien, Bogotá",
  "America/Lima": "Peru, Lima",
  "America/Caracas": "Venezuela, Caracas",
  "America/Santiago": "Chile, Santiago",
  "America/Argentina/Buenos_Aires": "Argentinien, Buenos Aires",
  "America/Sao_Paulo": "Brasilien, São Paulo, Rio de Janeiro, Brasília",
  "America/Montevideo": "Uruguay, Montevideo",
  // Asien
  "Asia/Jerusalem": "Israel, Jerusalem, Tel Aviv",
  "Asia/Beirut": "Libanon, Beirut",
  "Asia/Amman": "Jordanien, Amman",
  "Asia/Baghdad": "Irak, Bagdad",
  "Asia/Riyadh": "Saudi-Arabien, Riad",
  "Asia/Tehran": "Iran, Teheran",
  "Asia/Dubai": "Vereinigte Arabische Emirate, Dubai, Abu Dhabi, Emirate",
  "Asia/Tbilisi": "Georgien, Tiflis",
  "Asia/Yerevan": "Armenien, Eriwan",
  "Asia/Baku": "Aserbaidschan, Baku",
  "Asia/Karachi": "Pakistan, Karatschi",
  "Asia/Kabul": "Afghanistan, Kabul",
  "Asia/Tashkent": "Usbekistan, Taschkent",
  "Asia/Almaty": "Kasachstan, Alma-Ata",
  "Asia/Kolkata": "Indien, Neu-Delhi, Delhi, Mumbai, Kalkutta, Bangalore",
  "Asia/Calcutta": "Indien, Neu-Delhi, Delhi, Mumbai, Kalkutta",
  "Asia/Kathmandu": "Nepal, Kathmandu",
  "Asia/Dhaka": "Bangladesch, Dhaka",
  "Asia/Colombo": "Sri Lanka, Colombo",
  "Asia/Bangkok": "Thailand, Bangkok",
  "Asia/Ho_Chi_Minh": "Vietnam, Ho-Chi-Minh-Stadt, Saigon, Hanoi",
  "Asia/Jakarta": "Indonesien, Jakarta",
  "Asia/Singapore": "Singapur",
  "Asia/Kuala_Lumpur": "Malaysia, Kuala Lumpur",
  "Asia/Manila": "Philippinen, Manila",
  "Asia/Hong_Kong": "Hongkong",
  "Asia/Shanghai": "China, Peking, Beijing, Schanghai",
  "Asia/Taipei": "Taiwan, Taipeh",
  "Asia/Seoul": "Südkorea, Seoul, Korea",
  "Asia/Tokyo": "Japan, Tokio, Osaka",
  "Asia/Vladivostok": "Russland, Wladiwostok",
  // Afrika
  "Africa/Cairo": "Ägypten, Kairo",
  "Africa/Johannesburg": "Südafrika, Johannesburg, Kapstadt",
  "Africa/Lagos": "Nigeria, Lagos",
  "Africa/Nairobi": "Kenia, Nairobi",
  "Africa/Casablanca": "Marokko, Casablanca",
  "Africa/Algiers": "Algerien, Algier",
  "Africa/Tunis": "Tunesien, Tunis",
  "Africa/Addis_Ababa": "Äthiopien, Addis Abeba",
  "Africa/Accra": "Ghana, Accra",
  // Ozeanien
  "Australia/Sydney": "Australien, Sydney, Canberra, Melbourne",
  "Australia/Melbourne": "Australien, Melbourne",
  "Australia/Brisbane": "Australien, Brisbane, Queensland",
  "Australia/Perth": "Australien, Perth",
  "Australia/Adelaide": "Australien, Adelaide",
  "Australia/Darwin": "Australien, Darwin",
  "Pacific/Auckland": "Neuseeland, Auckland, Wellington",
  "Pacific/Fiji": "Fidschi",
};

const NAMES: Record<string, string[]> = Object.fromEntries(
  Object.entries(RAW).map(([zone, text]) => [zone, text.split(",").map((part) => part.trim()).filter(Boolean)]),
);

/** Die deutsche Beschriftung einer Zone („Österreich, Wien“) oder `null`, wenn keine bekannt ist. */
export function germanZoneLabel(zone: string): string | null {
  const names = NAMES[zone];
  if (!names) return null;
  // Land und wichtigste Stadt reichen als Beschriftung; der Rest dient nur der Suche.
  return names.slice(0, 2).join(", ");
}

/** Alle deutschen Suchbegriffe einer Zone (leer, wenn keine bekannt sind). */
export function germanZoneTerms(zone: string): string[] {
  return NAMES[zone] ?? [];
}

/** Ohne Suche zuerst angezeigt: die Zonen, die die meisten Leute brauchen. */
export const COMMON_ZONES = [
  "Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "UTC", "Europe/London", "Europe/Paris", "Europe/Rome",
  "Europe/Madrid", "Europe/Amsterdam", "Europe/Brussels", "Europe/Warsaw", "Europe/Prague", "Europe/Copenhagen",
  "Europe/Stockholm", "Europe/Athens", "Europe/Istanbul", "Europe/Moscow", "America/New_York", "America/Chicago",
  "America/Los_Angeles", "Asia/Dubai", "Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
];

/**
 * Für die Suche vereinfachte Schreibweise: klein, ohne Akzente und Umlautpunkte, „ß“ als „ss“, und die
 * Ersatzschreibung („ae“, „oe“, „ue“) wie der Umlaut selbst. So findet „zuerich“ wie „Zürich“ dieselbe Zone.
 */
export function foldForSearch(text: string): string {
  return text
    .toLowerCase()
    .replace(/ß/g, "ss")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/ae/g, "a")
    .replace(/oe/g, "o")
    .replace(/ue/g, "u")
    .replace(/[_/-]/g, " ");
}
