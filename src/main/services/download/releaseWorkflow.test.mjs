import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const retryScript = fileURLToPath(
  new URL('../../../../.github/scripts/build-with-retries.sh', import.meta.url)
)
const runBuild = (command) =>
  spawnSync('bash', ['-c', 'sleep() { :; }; export -f sleep; bash "$1"', 'test', retryScript], {
    env: { ...process.env, BUILD_COMMAND: command },
    encoding: 'utf8'
  })

test('failed release builds return failure after all retries', () => {
  const result = runBuild('echo ATTEMPT; false')
  assert.equal(result.status, 1)
  assert.equal(result.stdout.match(/ATTEMPT/g).length, 3)
})

test('successful builds stop retrying and command prefixes must succeed', () => {
  assert.equal(runBuild('true').status, 0)
  assert.equal(runBuild('false && true').status, 1)
  execFileSync('bash', ['-n', retryScript])
})

test('release workflow preserves existing releases and tags', () => {
  const workflow = readFileSync(
    new URL('../../../../.github/workflows/release.yml', import.meta.url),
    'utf8'
  )
  assert.doesNotMatch(workflow, /gh release delete|gh api -X DELETE/)
  assert.match(workflow, /Refusing to modify published release/)
  assert.match(workflow, /Existing tag .*different commit/)
  assert.match(workflow, /if-no-files-found: error/)
})
