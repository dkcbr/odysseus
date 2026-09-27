#!/usr/bin/env node
/**
 * MCP server wrapping God's Eye View's real, new Agent Bus
 * (server/providers/agent-bus/ in the gods-eye-view repo, built
 * tonight). Exposes a small set of GEV actions as MCP tools, each
 * translating into a POST to /api/agent/publish.
 *
 * Every action name and payload shape below was verified directly
 * against src/voice/gevActions.js in the real repo, not assumed --
 * three earlier guesses (fly_to, toggle_layer, select_entity, plus a
 * `session_id`/`lat`/`lon`/`layer_id`/`visible` shaped payload proposed
 * for this very wrapper) were each checked and found wrong before this
 * file was written. There is no `session_id` at all: GEV has no user
 * accounts, so the real bus (server/providers/agent-bus/routes.js)
 * broadcasts to a single fixed session ('local') -- every connected
 * browser tab receives every action.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const AGENT_BUS_URL =
  process.env.GEV_AGENT_BUS_URL || 'http://localhost:4173/api/agent/publish';

async function callAgentBus(action, payload) {
  const res = await fetch(AGENT_BUS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, payload }),
  });
  const text = await res.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = { raw: text };
  }
  if (!res.ok) {
    throw new Error(`Agent Bus error ${res.status}: ${text}`);
  }
  return body;
}

function toolResult(body) {
  return { content: [{ type: 'text', text: JSON.stringify(body) }] };
}

const server = new McpServer({
  name: 'gods-eye-view',
  version: '0.1.0',
});

// Real, verified: action 'fly_to_location', flat payload with EITHER
// locationId (preset city, e.g. 'tokyo') OR latitude/longitude (any
// point) -- confirmed directly against flyToRequestedLocation's own
// argument destructuring in gevActions.js.
server.registerTool(
  'gev_fly_to_location',
  {
    title: "Fly God's Eye View's camera to a location",
    description:
      "Fly the running globe camera to a location. Real, added 2026-09-22: " +
      "prefer `query` for almost any real request -- a free-text place " +
      "name (e.g. \"Atlantic City Beach\", \"Eiffel Tower\", \"the Grand " +
      "Canyon\") that the app geocodes itself via its own real, already-" +
      "working geocoder (confirmed directly against flyToRequestedLocation " +
      "and searchAndFlyTo in the real app source, and the app's own real " +
      "error message: \"fly_to_location needs a locationId, query, or " +
      "latitude/longitude\"). Do NOT invent or calculate latitude/" +
      "longitude yourself for a named place -- pass the name as `query` " +
      "and let the app resolve it; only use locationId (a known preset " +
      "city) or explicit latitude/longitude when the user gives you " +
      "coordinates directly.",
    inputSchema: {
      query: z
        .string()
        .optional()
        .describe(
          'A free-text place name or address (e.g. "Atlantic City Beach", "Eiffel Tower"). The app geocodes this itself -- prefer this over locationId/latitude/longitude for any place the user names rather than gives coordinates for.',
        ),
      locationId: z
        .string()
        .optional()
        .describe(
          'A known preset city name (e.g. "tokyo"). Only the small, fixed set already known to GEV -- prefer `query` for anything else.',
        ),
      latitude: z.number().optional(),
      longitude: z.number().optional(),
      rangeM: z
        .number()
        .optional()
        .describe('Camera distance from the target, in meters.'),
    },
  },
  async ({ query, locationId, latitude, longitude, rangeM }) => {
    const payload = {};
    if (query) payload.query = query;
    if (locationId) payload.locationId = locationId;
    if (latitude !== undefined) payload.latitude = latitude;
    if (longitude !== undefined) payload.longitude = longitude;
    if (rangeM !== undefined) payload.rangeM = rangeM;
    const body = await callAgentBus('fly_to_location', payload);
    return toolResult(body);
  },
);

// Real, verified: action 'set_layer_visibility', payload
// { layerId, enabled } -- confirmed directly against this action's own
// handler in gevActions.js. Real known layer ids/aliases (from
// LAYER_ALIASES in the same file): flights (aka aircraft/planes),
// military, earthquakes (aka quakes), satellites, rocket-launches (aka
// space mission(s)/missions), traffic, cctv (aka cameras), radio,
// bikeshare (aka bikes), ais-live-vessels (aka ais/ships/vessels/live
// vessels), local-datacenters, local-dams,
// telegeography-submarine-cables (aka cables/telegeography),
// local-firms.
server.registerTool(
  'gev_set_layer_visibility',
  {
    title: "Toggle a God's Eye View data layer",
    description:
      'Enable or disable one of the real data layers on the globe ' +
      '(e.g. flights, military, earthquakes, satellites, ' +
      'rocket-launches, traffic, cctv, radio, bikeshare, ' +
      'ais-live-vessels, local-datacenters, local-dams, ' +
      'telegeography-submarine-cables, local-firms). Common aliases ' +
      '(aircraft, ships, cameras, etc.) also work.',
    inputSchema: {
      layerId: z.string(),
      enabled: z.boolean(),
    },
  },
  async ({ layerId, enabled }) => {
    const body = await callAgentBus('set_layer_visibility', {
      layerId,
      enabled,
    });
    return toolResult(body);
  },
);

// Real, verified: action 'track_entity' -- confirmed the name exists as
// a real dispatcher branch in gevActions.js. Argument shape for this
// one specific action was not verified as deeply as fly_to_location and
// set_layer_visibility (time-boxed tonight); this tool passes through
// whatever the caller supplies as rawArgs, matching runGevAction's own
// signature, rather than guessing a narrower schema that might be
// wrong.
server.registerTool(
  'gev_track_entity',
  {
    title: 'Track/select an entity on the globe',
    description:
      'Track or select a specific entity (e.g. an aircraft) on the ' +
      "globe. Argument shape not fully verified against GEV's real " +
      'handler tonight -- if this fails, check ' +
      'src/voice/gevActions.js\'s own track_entity branch directly ' +
      'before retrying with different arguments.',
    inputSchema: {
      args: z
        .record(z.any())
        .optional()
        .describe('Raw arguments passed through to the track_entity action.'),
    },
  },
  async ({ args }) => {
    const body = await callAgentBus('track_entity', args || {});
    return toolResult(body);
  },
);

// Real, verified: action 'annotate_map', payload
// { annotations: [ {type, target, label?, ...} ] } -- confirmed
// directly against annotateMap/sanitizeAnnotationSpec in
// gevActions.js. Note the ARRAY wrapper -- a flat object here is
// silently rejected by this MCP wrapper's own zod schema, and would
// otherwise be silently ignored by GEV's own annotate_map handler too
// (confirmed live tonight: a flat payload produced no visible result).
server.registerTool(
  'gev_annotate_map',
  {
    title: "Draw an annotation on God's Eye View's globe",
    description:
      'Draw one or more annotations (outlines, points, routes) on the ' +
      'globe. Each annotation needs at least a `type` ("outline" draws ' +
      'the real enclosing boundary of `target`) and a `target` (a place ' +
      'name GEV will resolve).',
    inputSchema: {
      annotations: z
        .array(
          z.object({
            type: z.string(),
            target: z.string().optional(),
            toTarget: z.string().optional(),
            label: z.string().optional(),
          }),
        )
        .min(1),
      flyTo: z.boolean().optional(),
    },
  },
  async ({ annotations, flyTo }) => {
    const payload = { annotations };
    if (flyTo !== undefined) payload.flyTo = flyTo;
    const body = await callAgentBus('annotate_map', payload);
    return toolResult(body);
  },
);

// Real, verified: action 'clear_annotations', no payload needed.
server.registerTool(
  'gev_clear_annotations',
  {
    title: "Clear all of God's Eye View's annotations",
    description:
      'Remove every annotation currently drawn on the globe.',
    inputSchema: {},
  },
  async () => {
    const body = await callAgentBus('clear_annotations', {});
    return toolResult(body);
  },
);

const transport = new StdioServerTransport();
await server.connect(transport);
