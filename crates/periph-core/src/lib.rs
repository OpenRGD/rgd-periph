//! Peripheral Manager candidate domain model (DESIGN ONLY).
//!
//! Versioned internal Console contracts for observation-producing peripherals.
//! No API routes, systemd calls, device opens or active host controls exist.
//! The Somatic Runtime/Safety Gate remains the only physical motor authority.
//! UI/browser control is not authentication: enforce operator authorization
//! separately before admitting a future acquisition lifecycle action.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;

pub const DESCRIPTOR_SCHEMA: &str = "console.peripheral-descriptor.v0.1-candidate";
pub const STATUS_SCHEMA: &str = "console.peripheral-status.v0.1-candidate";
pub const ACTION_SCHEMA: &str = "console.peripheral-action.v0.1-candidate";
pub const RESULT_SCHEMA: &str = "console.peripheral-action-result.v0.1-candidate";

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct PeripheralDescriptor {
    pub schema_version: String,
    pub robot_id: String,
    pub peripheral_id: String,
    pub display_name: String,
    pub category: PeripheralCategory,
    pub source_kind: SourceKind,
    pub binding: DeviceBinding,
    pub publisher: PublisherDescriptor,
    pub streams: Vec<StreamDescriptor>,
    pub capabilities: Vec<Capability>,
    pub actuation_enabled: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PeripheralCategory {
    Imu,
    Camera,
    Microphone,
    Depth,
    Lidar,
    Environment,
    RangeSensor,
    Generic,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SourceKind {
    Physical,
    Simulated,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DeviceBinding {
    pub transport: TransportKind,
    pub binding_ref: String,
    pub verification: BindingVerification,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TransportKind {
    UsbSerial,
    UsbVideo,
    UsbAudio,
    Ethernet,
    I2c,
    Spi,
    Virtual,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BindingVerification {
    ConfiguredUnverified,
    OperatorVerified,
    NotConfigured,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PublisherDescriptor {
    pub publisher_id: String,
    pub host_id: String,
    pub manager: ManagerKind,
    pub service_ref: String,
    pub resource_lock: String,
    pub ownership: DeviceOwnership,
    pub startup_policy: StartupPolicy,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ManagerKind {
    SystemdUser,
    ExternalReadOnly,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DeviceOwnership {
    ExclusiveDeviceOwner,
    ExternallyManaged,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StartupPolicy {
    Manual,
    OnHostBootExplicit,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct StreamDescriptor {
    pub stream_id: String,
    pub sample_schema: String,
    pub data_class: DataClass,
    pub freshness_ms: u64,
    pub nominal_rate_hz: Option<f64>,
    pub fanout: Fanout,
    pub buffer_policy: BufferPolicy,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DataClass {
    SensorObservation,
    PrivacySensitiveMedia,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Fanout {
    MultipleReadOnlyConsumers,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BufferPolicy {
    LatestOnly,
    BoundedReplay,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
pub enum Capability {
    #[serde(rename = "observation.read")]
    ObservationRead,
    #[serde(rename = "acquisition.start")]
    AcquisitionStart,
    #[serde(rename = "acquisition.stop")]
    AcquisitionStop,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PeripheralStatus {
    pub schema_version: String,
    pub robot_id: String,
    pub peripheral_id: String,
    pub status_revision: u64,
    pub reported_at: String,
    pub device: DeviceStatus,
    pub publisher: PublisherStatus,
    pub streams: Vec<StreamStatus>,
    pub controls: ControlAvailability,
    pub actuation_enabled: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DeviceStatus {
    pub presence: DevicePresence,
    pub binding_verified: bool,
    pub checked_at: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DevicePresence {
    Present,
    Absent,
    Busy,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PublisherStatus {
    pub lifecycle: PublisherLifecycle,
    pub desired_state: DesiredState,
    pub manager_available: bool,
    pub enabled_on_boot: Option<bool>,
    pub reported_at: Option<String>,
    pub error_code: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PublisherLifecycle {
    NotInstalled,
    Stopped,
    Starting,
    Running,
    Stopping,
    Failed,
    Busy,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DesiredState {
    Running,
    Stopped,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct StreamStatus {
    pub stream_id: String,
    pub state: ObservationState,
    pub source_kind: SourceKind,
    pub last_received_at: Option<String>,
    pub age_ms: Option<u64>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ObservationState {
    Live,
    Stale,
    NoSource,
    Invalid,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ControlAvailability {
    pub can_start: bool,
    pub can_stop: bool,
    pub reason: ControlReason,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ControlReason {
    Ready,
    ManagerUnavailable,
    NotAuthorized,
    Unsupported,
    UnmanagedOwner,
    DeviceMissing,
    DeviceBusy,
    TransitionInProgress,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PeripheralAction {
    pub schema_version: String,
    pub request_id: String,
    pub robot_id: String,
    pub peripheral_id: String,
    pub action: AcquisitionAction,
    pub expected_status_revision: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub enum AcquisitionAction {
    #[serde(rename = "acquisition.start")]
    Start,
    #[serde(rename = "acquisition.stop")]
    Stop,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PeripheralActionResult {
    pub schema_version: String,
    pub request_id: String,
    pub robot_id: String,
    pub peripheral_id: String,
    pub action: AcquisitionAction,
    pub disposition: ActionDisposition,
    pub publisher_lifecycle: PublisherLifecycle,
    pub status_revision: u64,
    pub observed_at: String,
    pub error_code: Option<String>,
    pub actuation_enabled: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ActionDisposition {
    Accepted,
    NoOp,
    Rejected,
    Failed,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ValidationError {
    WrongSchema,
    WrongIdentity,
    MotorAuthorityNotAllowed,
    DuplicateCapability,
    DuplicateStream,
    DuplicateResourceOwner,
    InvalidStream,
    MissingCapability,
    NotManaged,
    SourceNotPhysical,
    StaleStatusRevision,
    ManagerUnavailable,
    DeviceUnavailable,
    BusyOrTransitioning,
    OperationNotAvailable,
    InconsistentStatus,
    InvalidRequestId,
}

pub fn validate_descriptor(d: &PeripheralDescriptor) -> Result<(), ValidationError> {
    if d.schema_version != DESCRIPTOR_SCHEMA {
        return Err(ValidationError::WrongSchema);
    }
    if d.actuation_enabled {
        return Err(ValidationError::MotorAuthorityNotAllowed);
    }
    if d.source_kind == SourceKind::Unknown {
        return Err(ValidationError::SourceNotPhysical);
    }
    if d.robot_id.is_empty()
        || d.peripheral_id.is_empty()
        || d.display_name.is_empty()
        || d.binding.binding_ref.is_empty()
        || d.publisher.service_ref.is_empty()
        || d.publisher.host_id.is_empty()
        || d.publisher.resource_lock.is_empty()
    {
        return Err(ValidationError::WrongIdentity);
    }
    if d.capabilities.len() > 12 || d.capabilities.is_empty() {
        return Err(ValidationError::MissingCapability);
    }
    let mut caps = HashSet::new();
    for cap in &d.capabilities {
        if !caps.insert(*cap) {
            return Err(ValidationError::DuplicateCapability);
        }
    }
    if !caps.contains(&Capability::ObservationRead) {
        return Err(ValidationError::MissingCapability);
    }
    let can_manage =
        caps.contains(&Capability::AcquisitionStart) || caps.contains(&Capability::AcquisitionStop);
    if can_manage
        && (d.publisher.manager != ManagerKind::SystemdUser
            || d.publisher.ownership != DeviceOwnership::ExclusiveDeviceOwner)
    {
        return Err(ValidationError::NotManaged);
    }
    if can_manage && d.source_kind != SourceKind::Physical {
        return Err(ValidationError::SourceNotPhysical);
    }
    if d.streams.is_empty() || d.streams.len() > 16 {
        return Err(ValidationError::InvalidStream);
    }
    let mut ids = HashSet::new();
    for stream in &d.streams {
        if !ids.insert(stream.stream_id.as_str()) {
            return Err(ValidationError::DuplicateStream);
        }
        if stream.sample_schema.is_empty()
            || !(50..=60_000).contains(&stream.freshness_ms)
            || stream
                .nominal_rate_hz
                .is_some_and(|hz| !hz.is_finite() || hz <= 0.0 || hz > 1000.0)
        {
            return Err(ValidationError::InvalidStream);
        }
    }
    Ok(())
}

/// A declared resource can be shared by multiple logical stream records
/// only if they all have the SAME service owner. Two separate publisher
/// identities cannot both claim a physical serial/camera device.
pub fn validate_registry(descriptors: &[PeripheralDescriptor]) -> Result<(), ValidationError> {
    let mut ids = HashSet::new();
    let mut resources = std::collections::HashMap::<(&str, &str), &str>::new();
    for d in descriptors {
        validate_descriptor(d)?;
        if !ids.insert((d.robot_id.as_str(), d.peripheral_id.as_str())) {
            return Err(ValidationError::WrongIdentity);
        }
        let resource = (
            d.publisher.host_id.as_str(),
            d.publisher.resource_lock.as_str(),
        );
        if let Some(owner) = resources.insert(resource, d.publisher.publisher_id.as_str()) {
            if owner != d.publisher.publisher_id {
                return Err(ValidationError::DuplicateResourceOwner);
            }
        }
    }
    Ok(())
}

pub fn validate_status(
    d: &PeripheralDescriptor,
    s: &PeripheralStatus,
) -> Result<(), ValidationError> {
    if s.schema_version != STATUS_SCHEMA {
        return Err(ValidationError::WrongSchema);
    }
    if s.robot_id != d.robot_id || s.peripheral_id != d.peripheral_id || s.reported_at.is_empty() {
        return Err(ValidationError::WrongIdentity);
    }
    if s.actuation_enabled {
        return Err(ValidationError::MotorAuthorityNotAllowed);
    }
    if !s.publisher.manager_available && (s.controls.can_start || s.controls.can_stop) {
        return Err(ValidationError::InconsistentStatus);
    }
    if s.device.presence == DevicePresence::Absent && s.controls.can_start {
        return Err(ValidationError::InconsistentStatus);
    }
    let mut streams = HashSet::new();
    for stream in &s.streams {
        let Some(spec) = d.streams.iter().find(|x| x.stream_id == stream.stream_id) else {
            return Err(ValidationError::InvalidStream);
        };
        if !streams.insert(stream.stream_id.as_str()) {
            return Err(ValidationError::DuplicateStream);
        }
        if stream.state == ObservationState::NoSource
            && (stream.last_received_at.is_some() || stream.age_ms.is_some())
        {
            return Err(ValidationError::InconsistentStatus);
        }
        if stream.state == ObservationState::Live
            && (stream.source_kind != d.source_kind
                || stream.last_received_at.is_none()
                || stream.age_ms.is_none_or(|age| age > spec.freshness_ms))
        {
            // Never publish a LIVE badge for a stale/undated measurement,
            // even when systemd confirms the acquisition service is running.
            return Err(ValidationError::InconsistentStatus);
        }
    }
    Ok(())
}

/// Pure admission decision only, NO process changes. Auth/CSRF and broker
/// peer credentials MUST be verified by the future API/controller as well.
/// A noop is safe only when the observed service already reached target.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Admission {
    DispatchToAuthorizedBroker,
    AlreadyAtRequestedState,
}

/// Same canonical ID shape as the JSON schema, not just 36 ASCII bytes.
fn request_id_ok(id: &str) -> bool {
    let b = id.as_bytes();
    b.len() == 36
        && [8, 13, 18, 23].iter().all(|index| b[*index] == b'-')
        && (0..36)
            .filter(|index| ![8, 13, 18, 23].contains(index))
            .all(|index| b[index].is_ascii_hexdigit())
        && (b'1'..=b'8').contains(&b[14])
        && matches!(b[19], b'8' | b'9' | b'a' | b'A' | b'b' | b'B')
}

pub fn admit_action(
    d: &PeripheralDescriptor,
    s: &PeripheralStatus,
    request: &PeripheralAction,
) -> Result<Admission, ValidationError> {
    validate_descriptor(d)?;
    validate_status(d, s)?;
    if request.schema_version != ACTION_SCHEMA {
        return Err(ValidationError::WrongSchema);
    }
    if request.robot_id != d.robot_id || request.peripheral_id != d.peripheral_id {
        return Err(ValidationError::WrongIdentity);
    }
    if !request_id_ok(&request.request_id) {
        return Err(ValidationError::InvalidRequestId);
    }
    if request.expected_status_revision != s.status_revision {
        return Err(ValidationError::StaleStatusRevision);
    }
    if !s.publisher.manager_available {
        return Err(ValidationError::ManagerUnavailable);
    }
    if s.device.presence == DevicePresence::Busy
        || s.publisher.lifecycle == PublisherLifecycle::Busy
    {
        return Err(ValidationError::BusyOrTransitioning);
    }
    let needed = match request.action {
        AcquisitionAction::Start => Capability::AcquisitionStart,
        AcquisitionAction::Stop => Capability::AcquisitionStop,
    };
    if !d.capabilities.contains(&needed) {
        return Err(ValidationError::MissingCapability);
    }
    match (request.action, s.publisher.lifecycle) {
        (AcquisitionAction::Start, PublisherLifecycle::Running)
        | (AcquisitionAction::Stop, PublisherLifecycle::Stopped) => {
            return Ok(Admission::AlreadyAtRequestedState)
        }
        (_, PublisherLifecycle::Starting | PublisherLifecycle::Stopping) => {
            return Err(ValidationError::BusyOrTransitioning)
        }
        (_, PublisherLifecycle::NotInstalled | PublisherLifecycle::Unknown) => {
            return Err(ValidationError::ManagerUnavailable)
        }
        _ => {}
    }
    if request.action == AcquisitionAction::Start
        && (d.binding.verification != BindingVerification::OperatorVerified
            || s.device.presence != DevicePresence::Present
            || !s.device.binding_verified)
    {
        return Err(ValidationError::DeviceUnavailable);
    }
    let permitted = match request.action {
        AcquisitionAction::Start => s.controls.can_start,
        AcquisitionAction::Stop => s.controls.can_stop,
    };
    if !permitted {
        return Err(ValidationError::OperationNotAvailable);
    }
    Ok(Admission::DispatchToAuthorizedBroker)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn imu() -> PeripheralDescriptor {
        serde_json::from_str(include_str!(
            "../../../examples/peripherals/paky01-imu.descriptor.json"
        ))
        .unwrap()
    }
    fn unavailable() -> PeripheralStatus {
        serde_json::from_str(include_str!(
            "../../../examples/peripherals/paky01-imu.manager-unavailable.status.json"
        ))
        .unwrap()
    }
    fn request() -> PeripheralAction {
        serde_json::from_str(include_str!(
            "../../../examples/peripherals/paky01-imu.start.action.json"
        ))
        .unwrap()
    }

    #[test]
    fn generic_imu_and_future_camera_use_same_descriptor() {
        let camera: PeripheralDescriptor = serde_json::from_str(include_str!(
            "../../../examples/peripherals/paky01-camera.future-example.descriptor.json"
        ))
        .unwrap();
        assert_eq!(validate_registry(&[imu(), camera]), Ok(()));
        assert_eq!(imu().streams[0].sample_schema, "console.imu.v0.1-candidate");
    }

    #[test]
    fn registry_cannot_double_claim_serial_by_different_publishers() {
        let first = imu();
        let mut other = first.clone();
        other.peripheral_id = "secondary-imu".into();
        other.publisher.publisher_id = "unmanaged-second-publisher".into();
        assert_eq!(
            validate_registry(&[first, other]),
            Err(ValidationError::DuplicateResourceOwner)
        );
    }

    #[test]
    fn no_fake_stop_and_no_automatic_healthy_when_broker_missing() {
        let d = imu();
        let s = unavailable();
        assert_eq!(validate_status(&d, &s), Ok(()));
        assert_eq!(
            admit_action(&d, &s, &request()),
            Err(ValidationError::ManagerUnavailable)
        );
        assert_eq!(s.streams[0].state, ObservationState::NoSource);
        assert!(!s.publisher.manager_available);
    }

    #[test]
    fn service_running_does_not_make_stream_live() {
        let d = imu();
        let mut s = unavailable();
        s.publisher.manager_available = true;
        s.publisher.lifecycle = PublisherLifecycle::Running;
        s.publisher.desired_state = DesiredState::Running;
        s.device.presence = DevicePresence::Present;
        s.device.binding_verified = true;
        s.controls = ControlAvailability {
            can_start: false,
            can_stop: true,
            reason: ControlReason::Ready,
        };
        assert_eq!(validate_status(&d, &s), Ok(()));
        assert_eq!(s.streams[0].state, ObservationState::NoSource);
        assert_eq!(
            admit_action(&d, &s, &request()),
            Ok(Admission::AlreadyAtRequestedState)
        );
    }

    #[test]
    fn revision_and_device_binding_gate_any_acquisition_start() {
        let mut d = imu();
        let mut s = unavailable();
        s.publisher.manager_available = true;
        s.publisher.lifecycle = PublisherLifecycle::Stopped;
        s.publisher.desired_state = DesiredState::Stopped;
        s.controls = ControlAvailability {
            can_start: true,
            can_stop: false,
            reason: ControlReason::Ready,
        };
        assert_eq!(
            admit_action(&d, &s, &request()),
            Err(ValidationError::DeviceUnavailable)
        );
        s.device.presence = DevicePresence::Present;
        s.device.binding_verified = true;
        // A runtime "present" reading does not automatically verify the
        // registered host binding path/identity.
        assert_eq!(
            admit_action(&d, &s, &request()),
            Err(ValidationError::DeviceUnavailable)
        );
        d.binding.verification = BindingVerification::OperatorVerified;
        assert_eq!(
            admit_action(&d, &s, &request()),
            Ok(Admission::DispatchToAuthorizedBroker)
        );
        let mut stale = request();
        stale.expected_status_revision = 9;
        assert_eq!(
            admit_action(&d, &s, &stale),
            Err(ValidationError::StaleStatusRevision)
        );
    }

    #[test]
    fn capability_is_explicit_not_implied_by_usb_presence() {
        let mut d = imu();
        d.capabilities
            .retain(|cap| *cap != Capability::AcquisitionStart);
        let mut s = unavailable();
        s.device.presence = DevicePresence::Present;
        s.device.binding_verified = true;
        s.publisher.lifecycle = PublisherLifecycle::Stopped;
        s.publisher.manager_available = true;
        s.controls.can_start = true;
        assert_eq!(
            admit_action(&d, &s, &request()),
            Err(ValidationError::MissingCapability)
        );
    }

    #[test]
    fn a_live_status_requires_a_measured_timestamp_under_stream_freshness() {
        let d = imu();
        let mut s = unavailable();
        s.publisher.lifecycle = PublisherLifecycle::Running;
        s.publisher.manager_available = true;
        s.streams[0].state = ObservationState::Live;
        s.streams[0].source_kind = SourceKind::Physical;
        s.streams[0].last_received_at = Some("2026-10-25T14:00:00Z".into());
        s.streams[0].age_ms = Some(3001);
        assert_eq!(
            validate_status(&d, &s),
            Err(ValidationError::InconsistentStatus)
        );
        s.streams[0].age_ms = Some(2999);
        assert_eq!(validate_status(&d, &s), Ok(()));
        s.streams[0].age_ms = None;
        assert_eq!(
            validate_status(&d, &s),
            Err(ValidationError::InconsistentStatus)
        );
    }

    #[test]
    fn rejected_uuid_and_unverified_binding_never_issue_command() {
        let d = imu();
        let mut s = unavailable();
        s.publisher.manager_available = true;
        s.publisher.lifecycle = PublisherLifecycle::Stopped;
        s.publisher.desired_state = DesiredState::Stopped;
        s.device.presence = DevicePresence::Present;
        s.device.binding_verified = true;
        s.controls.can_start = true;
        let mut cmd = request();
        cmd.request_id = "this-is-not-an-operator-uuid".into();
        assert_eq!(
            admit_action(&d, &s, &cmd),
            Err(ValidationError::InvalidRequestId)
        );
        assert_eq!(
            admit_action(&d, &s, &request()),
            Err(ValidationError::DeviceUnavailable)
        );
    }

    #[test]
    fn forbidden_actuation_is_not_a_peripheral_capability() {
        let mut d = imu();
        d.actuation_enabled = true;
        assert_eq!(
            validate_descriptor(&d),
            Err(ValidationError::MotorAuthorityNotAllowed)
        );
        assert!(serde_json::from_str::<PeripheralAction>(
            r#"{"schema_version":"console.peripheral-action.v0.1-candidate","request_id":"2bd48625-77e8-4a4b-ab5c-c910f0de2411","robot_id":"paky-01","peripheral_id":"torso-imu-01","action":"motor.enable","expected_status_revision":0}"#
        ).is_err());
    }

    #[test]
    fn action_result_is_separate_from_data_or_motor_readiness() {
        let result: PeripheralActionResult = serde_json::from_str(include_str!(
            "../../../examples/peripherals/paky01-imu.start-rejected.result.json"
        ))
        .unwrap();
        assert_eq!(result.schema_version, RESULT_SCHEMA);
        assert_eq!(result.disposition, ActionDisposition::Rejected);
        assert_eq!(result.publisher_lifecycle, PublisherLifecycle::NotInstalled);
        assert!(!result.actuation_enabled);
    }
}
