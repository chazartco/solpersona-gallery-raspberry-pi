/**
 * Countries offered for the Wi-Fi rules (the same list as the agent's setup
 * page, network.py). Until one is set the Pi uses the cautious world-wide
 * rules: 2.4 GHz only.
 */
export const COUNTRIES: [string, string][] = [
  ['US', 'United States'], ['GB', 'United Kingdom'], ['CA', 'Canada'], ['AU', 'Australia'], ['NZ', 'New Zealand'],
  ['IE', 'Ireland'], ['DE', 'Germany'], ['FR', 'France'], ['ES', 'Spain'], ['IT', 'Italy'], ['PT', 'Portugal'],
  ['NL', 'Netherlands'], ['BE', 'Belgium'], ['LU', 'Luxembourg'], ['CH', 'Switzerland'], ['AT', 'Austria'],
  ['DK', 'Denmark'], ['SE', 'Sweden'], ['NO', 'Norway'], ['FI', 'Finland'], ['IS', 'Iceland'], ['PL', 'Poland'],
  ['CZ', 'Czechia'], ['SK', 'Slovakia'], ['HU', 'Hungary'], ['RO', 'Romania'], ['BG', 'Bulgaria'], ['GR', 'Greece'],
  ['HR', 'Croatia'], ['SI', 'Slovenia'], ['EE', 'Estonia'], ['LV', 'Latvia'], ['LT', 'Lithuania'], ['UA', 'Ukraine'],
  ['TR', 'Türkiye'], ['IL', 'Israel'], ['AE', 'United Arab Emirates'], ['SA', 'Saudi Arabia'], ['ZA', 'South Africa'],
  ['NG', 'Nigeria'], ['KE', 'Kenya'], ['EG', 'Egypt'], ['MA', 'Morocco'], ['IN', 'India'], ['SG', 'Singapore'],
  ['MY', 'Malaysia'], ['TH', 'Thailand'], ['VN', 'Vietnam'], ['PH', 'Philippines'], ['ID', 'Indonesia'],
  ['HK', 'Hong Kong'], ['TW', 'Taiwan'], ['KR', 'South Korea'], ['JP', 'Japan'], ['CN', 'China'],
  ['MX', 'Mexico'], ['BR', 'Brazil'], ['AR', 'Argentina'], ['CL', 'Chile'], ['CO', 'Colombia'], ['PE', 'Peru'],
];

export function countryName(code: string | null | undefined): string {
  if (!code || code === '00') return 'not set (world-wide, 2.4 GHz only)';
  return COUNTRIES.find(([c]) => c === code)?.[1] ?? code;
}
