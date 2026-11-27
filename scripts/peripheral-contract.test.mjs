import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const read = path => readFileSync(new URL('../' + path, import.meta.url), 'utf8');
const json = path => JSON.parse(read(path));
const root = 'contracts/console/v1/';
const names = ['peripheral-descriptor', 'peripheral-status', 'peripheral-action', 'peripheral-action-result'];
const schema = Object.fromEntries(names.map(name => [name, json(root + name + '.v0.1-candidate.schema.json')]));
const fixtures = {
  imu: json('examples/peripherals/paky01-imu.descriptor.json'),
  camera: json('examples/peripherals/paky01-camera.future-example.descriptor.json'),
  unavailable: json('examples/peripherals/paky01-imu.manager-unavailable.status.json'),
  start: json('examples/peripherals/paky01-imu.start.action.json'),
  denied: json('examples/peripherals/paky01-imu.start-rejected.result.json'),
};

/** A small fixture checker for the explicitly used JSON Schema keywords.
 * Does not pretend to be a full JSON-Schema 2020-12 implementation.
 * Real API deserialization additionally uses serde deny_unknown_fields.
 */
function check(schemaPart, value, path = 'root') {
  const types = Array.isArray(schemaPart.type) ? schemaPart.type : schemaPart.type ? [schemaPart.type] : [];
  const gotType = value === null ? 'null' : Array.isArray(value) ? 'array' : Number.isInteger(value) ? 'integer' : typeof value;
  if (types.length) {
    assert.ok(types.includes(gotType) || (gotType === 'integer' && types.includes('number')), path + ': type mismatch');
  }
  if (Object.hasOwn(schemaPart, 'const')) assert.deepEqual(value, schemaPart.const, path + ': const mismatch');
  if (schemaPart.enum) assert.ok(schemaPart.enum.includes(value), path + ': unrecognized enum');
  if (schemaPart.pattern && typeof value === 'string') assert.match(value, new RegExp(schemaPart.pattern), path + ': pattern mismatch');
  if (schemaPart.minLength != null && typeof value === 'string') assert.ok(value.length >= schemaPart.minLength, path + ': short');
  if (schemaPart.maxLength != null && typeof value === 'string') assert.ok(value.length <= schemaPart.maxLength, path + ': long');
  if (schemaPart.format === 'date-time' && typeof value === 'string') assert.ok(Number.isFinite(Date.parse(value)), path + ': invalid timestamp');
  if (schemaPart.minimum != null && typeof value === 'number') assert.ok(value >= schemaPart.minimum, path + ': below min');
  if (schemaPart.maximum != null && typeof value === 'number') assert.ok(value <= schemaPart.maximum, path + ': exceeds max');
  if (schemaPart.exclusiveMinimum != null && typeof value === 'number') assert.ok(value > schemaPart.exclusiveMinimum, path + ': below exclusive min');
  if (schemaPart.type === 'array' && Array.isArray(value)) {
    if (schemaPart.minItems != null) assert.ok(value.length >= schemaPart.minItems, path + ': too few items');
    if (schemaPart.maxItems != null) assert.ok(value.length <= schemaPart.maxItems, path + ': too many items');
    if (schemaPart.uniqueItems) assert.equal(new Set(value.map(item => JSON.stringify(item))).size, value.length, path + ': duplicate items');
    if (schemaPart.items) value.forEach((item, i) => check(schemaPart.items, item, path + '[' + i + ']'));
  }
  if (schemaPart.type === 'object' && value && typeof value === 'object' && !Array.isArray(value)) {
    for (const name of schemaPart.required || []) assert.ok(Object.hasOwn(value, name), path + ': missing ' + name);
    if (schemaPart.additionalProperties === false) for (const name of Object.keys(value))
      assert.ok(Object.hasOwn(schemaPart.properties, name), path + ': unknown ' + name);
    for (const [name, inner] of Object.entries(schemaPart.properties || {}))
      if (Object.hasOwn(value, name)) check(inner, value[name], path + '.' + name);
  }
}

test('four generic contracts are separate, versioned and fail closed on extra fields', () => {
  for (const [name, def] of Object.entries(schema)) {
    assert.equal(def.$schema, 'https://json-schema.org/draft/2020-12/schema');
    assert.equal(def.additionalProperties, false);
    assert.equal(def.properties.schema_version.const, 'console.' + name + '.v0.1-candidate');
    assert.ok(def.required.includes('robot_id'));
    assert.ok(def.required.includes('peripheral_id'));
  }
  assert.deepEqual(schema['peripheral-action'].properties.action.enum, [
    'acquisition.start', 'acquisition.stop'
  ]);
  assert.equal(schema['peripheral-action-result'].properties.actuation_enabled.const, false);
  assert.equal(schema['peripheral-descriptor'].properties.actuation_enabled.const, false);
  assert.ok(!schema['peripheral-action'].properties.service);
  assert.ok(!schema['peripheral-action'].properties.command);
  assert.ok(!schema['peripheral-action'].properties.device_path);
});

test('real IMU and future camera conform to the same descriptor schema', () => {
  check(schema['peripheral-descriptor'], fixtures.imu);
  check(schema['peripheral-descriptor'], fixtures.camera);
  assert.equal(fixtures.imu.robot_id, fixtures.camera.robot_id);
  assert.equal(fixtures.imu.category, 'imu');
  assert.equal(fixtures.camera.category, 'camera');
  assert.equal(fixtures.imu.binding.transport, 'usb_serial');
  assert.equal(fixtures.camera.binding.transport, 'usb_video');
  assert.equal(fixtures.imu.streams[0].sample_schema, 'console.imu.v0.1-candidate');
  assert.equal(fixtures.camera.streams.length, 2);
  assert.equal(fixtures.camera.streams[0].data_class, 'privacy_sensitive_media');
  assert.deepEqual(fixtures.camera.capabilities, ['observation.read']); // not yet managed
  assert.equal(fixtures.camera.binding.verification, 'not_configured');
});

test('publisher lifecycle and observation freshness have independent status fields', () => {
  const status = fixtures.unavailable;
  check(schema['peripheral-status'], status);
  assert.equal(status.publisher.manager_available, false);
  assert.equal(status.publisher.lifecycle, 'not_installed');
  assert.equal(status.device.presence, 'unknown');
  assert.equal(status.streams[0].state, 'no_source');
  assert.equal(status.streams[0].age_ms, null);
  assert.equal(status.controls.can_start, false);
  assert.equal(status.controls.can_stop, false);
  assert.equal(status.actuation_enabled, false);
  assert.ok(schema['peripheral-status'].properties.publisher.properties.lifecycle.enum.includes('running'));
  assert.ok(schema['peripheral-status'].properties.streams.items.properties.state.enum.includes('no_source'));
});

test('only idempotent bounded operator acquisition actions are declared', () => {
  check(schema['peripheral-action'], fixtures.start);
  check(schema['peripheral-action-result'], fixtures.denied);
  assert.equal(fixtures.start.request_id, fixtures.denied.request_id);
  assert.equal(fixtures.start.action, fixtures.denied.action);
  assert.equal(fixtures.denied.disposition, 'rejected');
  assert.equal(fixtures.denied.publisher_lifecycle, 'not_installed');
  assert.equal(fixtures.denied.error_code, 'manager_not_installed');
  assert.ok(schema['peripheral-action'].required.includes('expected_status_revision'));
  assert.equal(schema['peripheral-action'].properties.action.enum.includes('power.off'), false);
  assert.equal(schema['peripheral-action'].properties.action.enum.includes('motor.enable'), false);
  assert.equal(schema['peripheral-action'].properties.action.enum.includes('publisher.exec'), false);
});

test('pure Rust domain validates cap/ownership/revision without host effects', () => {
  const model = read('crates/periph-core/src/lib.rs');
  assert.match(model, /pub fn validate_registry/);
  assert.match(model, /pub fn admit_action/);
  assert.match(model, /AlreadyAtRequestedState/);
  assert.match(model, /DuplicateResourceOwner/);
  assert.match(model, /SourceNotPhysical/);
  assert.match(model, /MotorAuthorityNotAllowed/);
  assert.doesNotMatch(model, /std::process::Command|std::process::Stdio|tokio::process|sudo|std::fs::write|libc::kill|serialport::new/);
});
