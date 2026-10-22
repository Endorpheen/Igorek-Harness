const {test}=require('node:test');
const assert=require('node:assert/strict');
const D=require('../error-diagnostics.js');
const support=require('../diagnostic-support.js');

test('tool context and an explicit failure cause survive into UI and support report',()=>{
  const error={code:'TOOL.INVALID_ARGUMENTS',component:'tool',operation:'execute',endpoint_role:'bridge_to_mobile',
    owner:'task:fixture',event_id:'fixture-error',tool_name:'host_service',tool_action:'files',service:'fixture',
    reason_code:'invalid_service_fields',reason_detail:'The call contains an invalid set of fields.'};
  const clean=D.clean(error);
  assert.match(D.message(clean,'en'),/host_service → fixture → files/);
  assert.match(D.message(clean,'en'),/invalid set of fields/);
  const report=support.createReport({local:{incidents:[{id:'fixture',diagnostic:error,state:'needs_owner'}]}},{version:'0.2.19'});
  assert.equal(report.local.incidents[0].error.reason_code,'invalid_service_fields');
  assert.equal(report.local.incidents[0].error.reason_detail,error.reason_detail);
});

test('explicit error details are bounded and redact credentials and URL query data',()=>{
  const error=D.clean({code:'HTTP.REQUEST_REJECTED',reason_detail:'api_key=EXCLUDED Bearer EXCLUDED sk-EXCLUDED https://user:EXCLUDED@fixture.test/path?token=EXCLUDED '+('x'.repeat(2000))});
  assert.ok(error.reason_detail.length<=512);
  assert.doesNotMatch(JSON.stringify(error),/EXCLUDED/);
  assert.match(D.message(error),/fixture.test\/path/);
});

test('missing details preserve the existing message without manufacturing a cause',()=>{
  assert.equal(D.message({code:'NET.READ_TIMEOUT'}),'Ответ не поступил вовремя.');
  assert.equal(D.clean({code:'TOOL.EXECUTION_FAILED',prompt:'EXCLUDED'}).reason_detail,undefined);
});
