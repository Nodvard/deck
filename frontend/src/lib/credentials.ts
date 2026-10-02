/**
 * Regeln fuer neue Benutzernamen und Passwoerter, im Browser vorab geprueft. Dieselben Regeln prueft
 * der Server (`services/auth.py::validate_new_username`, Laenge der Passwoerter in den Schemas);
 * hier kommen sie nur schneller und mit einem Satz, den die Person versteht, statt erst nach dem
 * Absenden als „HTTP 422“.
 *
 * Gilt nur fuer NEUE Konten: wer ein aelteres Konto mit Leerzeichen im Namen hat, kann sich weiter
 * anmelden (die Anmeldeseite prueft den Namen nicht).
 */

export const USERNAME_MIN_LENGTH = 3;
export const PASSWORD_MIN_LENGTH = 8;

export const USERNAME_HINT =
  "Nur Kleinbuchstaben, Ziffern sowie . - und _, mindestens 3 Zeichen, ohne Leerzeichen – z. B. „admin“. Großbuchstaben werden automatisch umgewandelt.";
export const PASSWORD_HINT = `Mindestens ${PASSWORD_MIN_LENGTH} Zeichen.`;

const USERNAME_PATTERN = /^[a-z0-9][a-z0-9._-]*$/;

/** Was am Namen nicht stimmt (als Satz), oder `null`, wenn er in Ordnung ist. Wie der Server: erst getrimmt und kleingeschrieben. */
export function usernameProblem(raw: string): string | null {
  const name = raw.trim().toLowerCase();
  if (name.length < USERNAME_MIN_LENGTH) return `Benutzername: Mindestens ${USERNAME_MIN_LENGTH} Zeichen.`;
  if (!USERNAME_PATTERN.test(name)) {
    return "Benutzername: Nur Kleinbuchstaben, Ziffern sowie . - und _ erlaubt, ohne Leerzeichen; er muss mit einem Buchstaben oder einer Ziffer beginnen.";
  }
  return null;
}

/** Was am Passwort nicht stimmt (als Satz), oder `null`. */
export function passwordProblem(password: string): string | null {
  return password.length < PASSWORD_MIN_LENGTH ? `Passwort: Mindestens ${PASSWORD_MIN_LENGTH} Zeichen.` : null;
}

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+$/;

/**
 * Was an der E-Mail-Adresse nicht stimmt (als Satz), oder `null`. Leer ist erlaubt (die Angabe ist
 * freiwillig). Der Server prueft die Adresse nicht; die Formulare haben `noValidate` (eigene Saetze
 * statt der Browser-Sprechblase), also faengt diese einfache Pruefung den offensichtlichen Tippfehler ab.
 */
export function emailProblem(raw: string): string | null {
  const email = raw.trim();
  if (!email || EMAIL_PATTERN.test(email)) return null;
  return "E-Mail: Das sieht nicht nach einer Adresse aus. Sie braucht ein @, z. B. name@beispiel.de.";
}
