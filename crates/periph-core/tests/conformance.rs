//! Shared managed-host policy vectors; pure domain admission only.
use rgd_periph_core::*;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashSet;

#[derive(Deserialize)]
struct Corpus {
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    id: String,
    input: Input,
    expected: Expected,
}

#[derive(Deserialize)]
struct Input {
    action: String,
    robot_id: String,
    peripheral_id: String,
    request_id: String,
    expected_revision: u64,
    observed_revision: u64,
    lifecycle: PublisherLifecycle,
    manager_available: bool,
    presence: DevicePresence,
    binding_verified: bool,
}

#[derive(Deserialize)]
struct Expected {
    decision: String,
    reason: Option<String>,
}

fn normalize(result: Result<Admission, ValidationError>) -> (&'static str, Option<&'static str>) {
    match result {
        Ok(Admission::DispatchToAuthorizedBroker) => ("dispatch", None),
        Ok(Admission::AlreadyAtRequestedState) => ("no_op", None),
        Err(error) => {
            let reason = match error {
                ValidationError::StaleStatusRevision => "stale_revision",
                ValidationError::ManagerUnavailable => "manager_unavailable",
                ValidationError::BusyOrTransitioning => "transition",
                ValidationError::DeviceUnavailable => "device_unavailable",
                ValidationError::WrongIdentity => "scope",
                ValidationError::InvalidRequestId => "request_id",
                ValidationError::InvalidRevision => "invalid_revision",
                ValidationError::RevisionExhausted => "revision_exhausted",
                other => panic!("unmapped domain rejection: {other:?}"),
            };
            ("reject", Some(reason))
        }
    }
}

#[test]
fn shared_managed_host_policy_vectors() {
    let wire_json = include_str!("../../../conformance/wire-cases.json");
    let policy_json = include_str!("../../../conformance/policy-cases.json");
    let wire: Value = serde_json::from_str(wire_json).unwrap();
    let corpus: Corpus = serde_json::from_str(policy_json).unwrap();
    assert_eq!(corpus.cases.len(), 32);
    let ids: HashSet<_> = corpus.cases.iter().map(|case| &case.id).collect();
    assert_eq!(ids.len(), corpus.cases.len());
    let descriptor: PeripheralDescriptor =
        serde_json::from_value(wire["documents"]["descriptor"].clone()).unwrap();

    for case in corpus.cases {
        let i = case.input;
        let mut status: PeripheralStatus =
            serde_json::from_value(wire["documents"]["status"].clone()).unwrap();
        let mut action: PeripheralAction =
            serde_json::from_value(wire["documents"]["action"].clone()).unwrap();
        status.status_revision = i.observed_revision;
        status.publisher.lifecycle = i.lifecycle;
        status.publisher.manager_available = i.manager_available;
        status.device.presence = i.presence;
        status.device.binding_verified = i.binding_verified;
        // Explicit synthetic host-registration projection. It does not grant
        // operator authorization or imply that the host sees stream metadata.
        status.controls.can_start = i.manager_available && i.presence != DevicePresence::Absent;
        status.controls.can_stop = i.manager_available;
        action.action = serde_json::from_value(Value::String(i.action)).unwrap();
        action.robot_id = i.robot_id;
        action.peripheral_id = i.peripheral_id;
        action.request_id = i.request_id;
        action.expected_status_revision = i.expected_revision;
        let actual = normalize(admit_action(&descriptor, &status, &action));
        let expected = (
            case.expected.decision.as_str(),
            case.expected.reason.as_deref(),
        );
        assert_eq!(actual, expected, "{}", case.id);
    }
}
