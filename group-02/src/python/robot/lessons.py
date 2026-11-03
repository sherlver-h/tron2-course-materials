"""Small functions intended for explanation and student edits."""
import math
import time

from client import ARM_NAMES, ProtocolError, map_state, positive, pose_errors

# Official DACH_TRON2A desire_joint03; right mirrors [1,-1,-1,1,-1,1,-1].
HOME = [0., .25, 0., -1.57, .236, 0., 0., 0., -.25, 0., -1.57, -.236, 0., 0.]
PREPARE_LEFT = [[0., 0., 1.48, 0., 0., 0., 0.],
                [0., 0., 1.48, -1.57, 0., 0., 0.], HOME[:7]]
ZERO = [0.]*7  # all joints 0: arms hanging, the power-on state seen on the real robot
ROUTES = {"direct": [HOME[:7]], "table": PREPARE_LEFT, "zero": [ZERO]}


def prepare(client, duration=5., route="direct"):
    """Explicit DACH simulation preparation; never silently clamp measured q."""
    initial = stationary_q(client)
    points = ROUTES[route]
    stages = []
    for left in points:
        target = left + [q*sign for q, sign in zip(left, [1,-1,-1,1,-1,1,-1])]
        # Preparation may be a large move; on a real robot the operator confirms it.
        accepted = client.movej(target, duration, large_move=True)
        arrival = client.wait_joints(target, timeout=duration+10., min_duration=duration)
        stages.append(dict(target=target, accepted=accepted, arrival=arrival))
    return dict(initial=initial, stages=stages, route=route,
                target_source="official DACH_TRON2A desire_joint01/02/03; direct uses 03; zero = all joints 0")


def checked_offset_start(client, include_head=False):
    initial = stationary_q(client, include_head=include_head)
    try:
        client.arm_target(initial[:14])
    except ValueError as exc:
        raise ValueError(f"Measured state outside command limits; no target was clamped. "
                         f"Run explicit DACH simulation prepare first. {exc}") from exc
    return initial


def prepare_head(client, duration=5.):
    """Explicit MoveH to neutral head; required only before a head-limited ServoJ."""
    initial = stationary_q(client, include_head=True)
    target = [0., 0.]  # EDIT: explicit pitch/yaw command, not a clamp of feedback.
    accepted = client.moveh(target, duration)
    started, stable, trace = time.monotonic(), 0, []
    deadline = started+duration+10.
    while time.monotonic() < deadline:
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            break
        state = client.joint_state(timeout=remaining)
        actual = map_state(state, include_head=True, names=client.joint_names)
        dq = map_state(state, 'dq', include_head=True, names=client.joint_names)
        error = max(abs(a-b) for a,b in zip(actual[14:],target))
        speed = max(abs(x) for x in dq[14:])
        elapsed = time.monotonic()-started
        stable = stable+1 if elapsed >= duration and error <= .02 and speed <= .05 else 0
        trace.append(dict(elapsed_s=elapsed, head_error_rad=error, head_speed_rad_s=speed))
        if stable >= 3:
            return dict(initial=initial, target_head=target, final=actual, accepted=accepted,
                        arrival=dict(status='arrived', samples=len(trace), trace=trace),
                        observed_arm_change_rad=max(abs(a-b) for a,b in zip(initial[:14], actual[:14])))
        time.sleep(min(.05, max(0., deadline-time.monotonic())))
    raise TimeoutError('MoveH head arrival not confirmed')


def stationary_q(client, include_head=False):
    state = client.joint_state()
    if max(abs(x) for x in map_state(state, "dq", names=client.joint_names)) > .05:
        raise ProtocolError("wait for stationary arms before constructing an offset")
    return map_state(state, include_head=include_head, names=client.joint_names)


def joint_offset(client, side="left", joint=3, delta=.06, duration=3.):
    """Change one joint; retain every other measured joint target."""
    initial = checked_offset_start(client)
    target = initial.copy()
    index = joint + (0 if side == "left" else 7)
    target[index] += delta                     # EDIT: selected joint displacement in rad.
    accepted = client.movej(target, duration)  # ACK only: command accepted.
    arrived = client.wait_joints(target, timeout=duration+10., min_duration=duration)
    return dict(initial=initial, target=target, accepted=accepted, arrival=arrived)


def pose_offset(client, side="left", axis=0, delta=.01, duration=3.):
    """Use the SDK's measured frame; retain both orientations and other arm."""
    stationary_q(client)
    initial = client.move_pose()
    target = initial.copy()
    index = axis + (0 if side == "left" else 7)
    target[index] += delta                       # EDIT: XYZ offset in SDK base frame, m.
    accepted = client.movep(target[:7], target[7:], duration)
    arrived = client.wait_pose(target, timeout=duration+10., min_duration=duration,
                               position_tolerance=.003)
    final = client.move_pose()
    return dict(initial=initial, target=target, accepted=accepted, arrival=arrived,
                final=final, observed_translation_m=[math.dist(initial[o:o+3], final[o:o+3]) for o in (0,7)],
                frame="SDK base-bottom center / last-joint end frame; XYZ+WXYZ")


def wait_pose_settled(client, min_duration, timeout, still=.0003, samples=5, poll=.2):
    """Real robot dq reads 0, so 'stopped' = pose changes <= still (m) over consecutive polls."""
    started, last, stable, trace = time.monotonic(), None, 0, []
    while time.monotonic() < started+timeout:
        pose = client.move_pose()
        elapsed = time.monotonic()-started
        change = None if last is None else max(math.dist(pose[o:o+3], last[o:o+3]) for o in (0, 7))
        trace.append(dict(elapsed_s=elapsed, change_m=change))
        stable = stable+1 if change is not None and elapsed >= min_duration and change <= still else 0
        if stable >= samples:
            return pose, trace
        last = pose
        time.sleep(poll)
    raise TimeoutError(f"pose did not settle within {timeout} s")


def pose_offset_corrected(client, side="left", axis=0, delta=.005, duration=5.,
                          corrections=2, gain=.5, tolerance=.003, max_correction=.01):
    """MoveP, then outer-loop correction: re-measure and shift the command by gain*residual.

    Experiment for a real controller whose MoveP stops short. Stops when the error
    is within tolerance, grows, or the command would drift max_correction from target.
    """
    stationary_q(client)
    initial = client.move_pose()
    target = initial.copy()
    target[axis + (0 if side == "left" else 7)] += delta   # EDIT: XYZ offset in SDK base frame, m.
    command, steps = target.copy(), []
    for step in range(corrections+1):
        accepted = client.movep(command[:7], command[7:], duration)
        measured, trace = wait_pose_settled(client, duration, duration+15.)
        position, angle = pose_errors(target, measured)
        steps.append(dict(step=step, command=command.copy(), accepted=accepted, measured=measured,
                          position_error_m=position, orientation_error_rad=angle, settle_samples=len(trace)))
        if position <= tolerance:
            return dict(status="arrived", initial=initial, target=target, steps=steps,
                        corrections_used=step, frame="SDK base-bottom center / last-joint end frame; XYZ+WXYZ")
        if step and position >= steps[-2]["position_error_m"]:
            raise TimeoutError(f"correction not reducing error ({position*1000:.1f} mm); stopped. steps={steps}")
        for o in (0, 7):   # EDIT: correct positions only; orientation stays at the target.
            command[o:o+3] = [c+gain*(t-m) for c, t, m in zip(command[o:o+3], target[o:o+3], measured[o:o+3])]
        if max(math.dist(command[o:o+3], target[o:o+3]) for o in (0, 7)) > max_correction:
            raise ValueError(f"correction would move command > {max_correction} m from target; stopped")
    raise TimeoutError(f"not within {tolerance*1000:.0f} mm after {corrections} corrections; "
                       f"last error {steps[-1]['position_error_m']*1000:.1f} mm. steps={steps}")


def servo_offset(client, side="left", joint=3, delta=.06, duration=3., rate=300.):
    """Finite quintic reference; caller paces ServoJ with a monotonic clock.

    This runs on an ordinary Python host. Its measured jitter is an observation,
    not proof of a hard real-time control loop. No catch-up command bursts.
    """
    initial = checked_offset_start(client, include_head=True)
    target = initial.copy()
    target[joint + (0 if side == "left" else 7)] += delta
    client.arm_target(target[:14])
    start, period = time.monotonic(), 1./rate
    next_send, timestamps = start, []
    while True:
        now = time.monotonic()
        if now < next_send:
            time.sleep(next_send-now)
        now = time.monotonic()
        fraction = min(1., (now-start)/duration)
        alpha = 10*fraction**3 - 15*fraction**4 + 6*fraction**5
        reference = [a+alpha*(b-a) for a, b in zip(initial, target)]
        client.servoj(reference)                # EDIT: interpolate the reference, not measured state.
        timestamps.append(time.monotonic())
        if fraction >= 1.:
            break
        next_send = start + (math.floor((time.monotonic()-start)/period)+1)*period
    intervals = [b-a for a, b in zip(timestamps, timestamps[1:])]
    arrival = client.wait_joints(target[:14], timeout=10.)
    return dict(initial=initial, target=target, arrival=arrival,
                requested_hz=rate, sent=len(timestamps),
                mean_interval_ms=1000*sum(intervals)/len(intervals) if intervals else None,
                max_interval_ms=1000*max(intervals) if intervals else None,
                samples_over_1_5_period=sum(x>1.5*period for x in intervals),
                hard_realtime_guarantee=False, intervals_s=intervals)


def gripper_opening(client, side, opening, speed=30., force=20., timeout=5.):
    accepted = client.gripper(side, opening, speed, force)
    deadline, stable, trace = time.monotonic()+positive(timeout, "timeout"), 0, []
    while time.monotonic() < deadline:
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            break
        state = client.request("request_get_limx_2fclaw_state", timeout=remaining)
        actual = float(state[f"{side}_opening"])
        if not math.isfinite(actual):
            raise ProtocolError("non-finite gripper feedback")
        trace.append(actual)
        stable = stable+1 if abs(actual-opening) <= 2. else 0
        if stable >= 3:
            return dict(accepted=accepted, status="opening_observed", trace=trace,
                        grasp_contact_verified=False)
        time.sleep(min(.05, max(0., deadline-time.monotonic())))
    raise TimeoutError(f"gripper opening not confirmed; observed={trace[-3:]}")
