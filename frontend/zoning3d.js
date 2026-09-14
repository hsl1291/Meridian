/* ============================================================
   Zoning intensity and massing.

   Miami 21 is a form-based code, so the district name carries the intensity:
   T6-8 and T6-80 are the same transect and eight versus eighty storeys. The
   flat category palette painted them identically, which for a termination
   screen hides the one attribute that decides whether a site can be rebuilt
   bigger than what is standing.

   `max_stories` arrives on every zoning feature from the server, which reads it
   off the district code and sends null where the code declares no height. Two
   consumers here: a lightness ramp inside the family colour, and an extrusion
   height. Both must treat null as "unknown" rather than "zero" -- a flat grey
   polygon and a zero-height building are different claims.

   Pure functions, no DOM and no map object, so they run under `node --test`.
   ============================================================ */
(function (root) {
  'use strict';

  // Feet per storey for the massing. A screening figure: Miami 21 sets maximum
  // heights in storeys, not feet, and the conversion varies by transect and by
  // whether the ground floor is commercial.
  const FEET_PER_STOREY = 10;

  // The ramp runs over the range the codes actually span, so the tiers separate
  // instead of bunching at one end: T3 is 2 storeys, T6-80 is 80.
  const RAMP_MIN = 2;
  const RAMP_MAX = 80;

  /** #rrggbb -> {r,g,b} */
  function parseHex(hex) {
    const h = String(hex).trim().replace(/^#/, '');
    const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
    if (!/^[0-9a-f]{6}$/i.test(full)) return null;
    return {
      r: parseInt(full.slice(0, 2), 16),
      g: parseInt(full.slice(2, 4), 16),
      b: parseInt(full.slice(4, 6), 16),
    };
  }

  function toHex({ r, g, b }) {
    const c = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
    return '#' + c(r) + c(g) + c(b);
  }

  /**
   * Darken a family colour in proportion to storeys.
   *
   * Lightness rather than hue, so the ten-family legend still reads as ten
   * families -- a T6-80 is a darker downtown, not a new colour. `amount` is the
   * share of the way to black at the top of the ramp.
   */
  function shade(hex, stories, amount = 0.45) {
    const c = parseHex(hex);
    if (!c) return hex;
    if (stories == null || !isFinite(stories)) return hex;   // unknown stays flat
    const span = RAMP_MAX - RAMP_MIN;
    const t = Math.max(0, Math.min(1, (stories - RAMP_MIN) / span));
    const k = 1 - amount * t;
    return toHex({ r: c.r * k, g: c.g * k, b: c.b * k });
  }

  /** Storeys -> extrusion height in metres (MapLibre's unit). */
  function extrusionMetres(stories) {
    if (stories == null || !isFinite(stories) || stories <= 0) return 0;
    return stories * FEET_PER_STOREY * 0.3048;
  }

  /**
   * A MapLibre paint expression that darkens each family by max_stories.
   *
   * Built from the palette rather than hand-written so a colour added to
   * ZONE_CAT_COLORS cannot be forgotten here. Expressed as a per-category
   * interpolate over max_stories; features with no storey count fall through to
   * the flat family colour.
   */
  function fillColorExpression(palette, stops = [2, 8, 24, 48, 80]) {
    const match = ['match', ['get', 'category']];
    for (const [cat, hex] of Object.entries(palette)) {
      if (cat === 'other') continue;
      const interp = ['interpolate', ['linear'], ['coalesce', ['get', 'max_stories'], 0]];
      // 0 means "no declared height" -- pin it to the undarkened family colour
      // so an unknown never reads as the lightest tier of a real ramp.
      interp.push(0, hex);
      for (const s of stops) interp.push(s, shade(hex, s));
      match.push(cat, interp);
    }
    match.push(palette.other || '#CFD4DA');
    return match;
  }

  /** Extrusion height expression, in metres, straight off max_stories. */
  function extrusionHeightExpression() {
    return ['*', ['coalesce', ['get', 'max_stories'], 0], FEET_PER_STOREY * 0.3048];
  }

  /**
   * Read pitch and bearing out of a URL hash, clamped to what MapLibre accepts.
   * Absent or unparseable values return null so the caller keeps its default
   * rather than snapping the camera flat.
   */
  function parseCamera(params) {
    const num = (key, lo, hi) => {
      const v = parseFloat(params.get(key));
      if (!isFinite(v)) return null;
      return Math.max(lo, Math.min(hi, v));
    };
    return { pitch: num('pitch', 0, 85), bearing: num('bearing', -180, 360) };
  }

  const api = {
    FEET_PER_STOREY, RAMP_MIN, RAMP_MAX,
    parseHex, toHex, shade, extrusionMetres,
    fillColorExpression, extrusionHeightExpression, parseCamera,
  };

  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.Zoning3D = api;
}(typeof globalThis !== 'undefined' ? globalThis : this));
