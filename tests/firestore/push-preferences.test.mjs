import { after, before, beforeEach, test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { initializeTestEnvironment, assertFails, assertSucceeds } from '@firebase/rules-unit-testing';
import { collection, doc, getDocs, setDoc, updateDoc, deleteDoc, Timestamp } from 'firebase/firestore';

// A demo project and synthetic registration only. Never contacts production.
let environment;
const token = 'fixture-capability-token';
const preference = enabled => ({push_enabled: enabled, push_updated_at: '2026-10-24T08:00:00+09:00'});
const registration = id => ({token: id, nickname: 'fixture', registered_at: '2026-10-24T08:00:00+09:00', createdAt: Timestamp.now(), approved: false});
const target = context => doc(context.firestore(), 'fcm_tokens', token);

before(async () => {
  environment = await initializeTestEnvironment({
    projectId: 'demo-sayo-preferences',
    firestore: {host: '127.0.0.1', port: 8786, rules: await readFile(new URL('../../firestore.rules', import.meta.url), 'utf8')},
  });
});
beforeEach(async () => {
  await environment.clearFirestore();
  await environment.withSecurityRulesDisabled(context => setDoc(target(context), {...registration(token), approved: true}));
});
after(async () => { await environment?.cleanup(); });

test('registered device can turn its push preference off and back on', async () => {
  const ref = target(environment.unauthenticatedContext());
  await assertSucceeds(updateDoc(ref, preference(false)));
  await assertSucceeds(updateDoc(ref, preference(true)));
});
test('push preference cannot alter approval', async () => {
  await assertFails(updateDoc(target(environment.unauthenticatedContext()), {...preference(false), approved: false}));
});
test('push preference cannot alter nickname or token', async () => {
  const ref = target(environment.unauthenticatedContext());
  await assertFails(updateDoc(ref, {...preference(false), nickname: 'changed'}));
  await assertFails(updateDoc(ref, {...preference(false), token: 'different-fixture'}));
});
test('malformed and incomplete preference writes fail', async () => {
  const ref = target(environment.unauthenticatedContext());
  await assertFails(updateDoc(ref, {push_enabled: 'false', push_updated_at: 'fixture'}));
  await assertFails(updateDoc(ref, {push_enabled: false}));
  await assertFails(updateDoc(ref, {push_enabled: false, push_updated_at: 'x'.repeat(41)}));
});
test('device cannot list registrations or delete a registration', async () => {
  const context = environment.unauthenticatedContext();
  await assertFails(getDocs(collection(context.firestore(), 'fcm_tokens')));
  await assertFails(deleteDoc(target(context)));
});
test('new registration accepts optional boolean push preference without approval', async () => {
  const id = 'new-fixture';
  await assertSucceeds(setDoc(doc(environment.unauthenticatedContext().firestore(), 'fcm_tokens', id), {...registration(id), ...preference(false)}));
});
test('new registration cannot approve itself or inject administrator fields', async () => {
  const context = environment.unauthenticatedContext();
  await assertFails(setDoc(doc(context.firestore(), 'fcm_tokens', 'self-approved'), {...registration('self-approved'), approved: true, ...preference(true)}));
  await assertFails(setDoc(doc(context.firestore(), 'fcm_tokens', 'admin-injection'), {...registration('admin-injection'), admin: true}));
});
test('admin approval remains a separate narrowly scoped update', async () => {
  const ref = target(environment.authenticatedContext('fixture-admin', {admin: true}));
  await assertSucceeds(updateDoc(ref, {approved: false}));
  await assertFails(updateDoc(ref, {...preference(true), approved: true}));
});
