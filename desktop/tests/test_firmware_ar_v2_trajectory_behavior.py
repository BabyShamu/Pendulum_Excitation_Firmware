import math
import unittest


START_HZ = 150.0
MAX_HZ = 4000.0
ACCEL_HZ_S = 18000.0
STEP_PULSE_US = 5
DIR_SETUP_US = 10
MAX_COMMAND_MM = 6.0
STEPS_PER_MM = 40.0


class ArV2Waveform:
    """Numerical equivalent of ARV2_Step for offline trajectory testing."""

    def __init__(self) -> None:
        self.dc = 0.0
        self.previous_ac = 0.0
        self.quadrature = 0.0
        self.direct_gain = 0.0
        self.quadrature_gain = 0.0
        self.initialized = False
        self.command_mm = 0.0

    def step(self, theta_rad: float, amplitude_mm: float, phase: float, mode: int) -> float:
        theta2 = theta_rad * theta_rad
        if not self.initialized:
            self.dc = theta2
            self.initialized = True
            return 0.0
        self.dc += 0.005 / (0.005 + 2.0) * (theta2 - self.dc)
        ac = theta2 - self.dc
        self.quadrature = 0.9048 * self.quadrature + 20.0 * (ac - self.previous_ac)
        self.previous_ac = ac
        magnitude = math.hypot(ac, self.quadrature)
        if magnitude < 0.0001:
            desired = 0.0
        else:
            self.quadrature_gain = max(
                -6.9077553,
                min(6.9077553, self.quadrature_gain - 0.0005 * math.log(
                    max((self.quadrature * math.exp(self.quadrature_gain)) ** 2, 1e-20)
                )),
            )
            self.direct_gain = max(
                -6.9077553,
                min(6.9077553, self.direct_gain - 0.0005 * math.log(
                    max((ac * math.exp(self.direct_gain)) ** 2, 1e-20)
                )),
            )
            alpha = math.atan2(
                self.quadrature * math.exp(self.quadrature_gain),
                -ac * math.exp(self.direct_gain),
            ) + phase
            h1 = math.sin(alpha)
            h3 = math.sin(3.0 * alpha)
            h5 = math.sin(5.0 * alpha)
            if mode == 1:
                wave = h1
            elif mode == 2:
                wave = 1.15470053838 * (h1 + h3 / 6.0)
            else:
                wave = 1.20713 * h1 + 0.28101 * h3 + 0.07389 * h5
            desired = amplitude_mm * max(-1.0, min(1.0, wave))
        slew = 250.0 * 0.005
        self.command_mm += max(-slew, min(slew, desired - self.command_mm))
        return self.command_mm


class ParametricV2Trajectory:
    """Event-driven model of ParametricV2OnTimer and its TIM6 phases."""

    def __init__(self, position: int = 0) -> None:
        self.position = position
        self.target = position
        self.speed = 0.0
        self.direction = 0
        self.phase = "idle"
        self.now_us = 0
        self.next_event_us = None
        self.fault = None
        self.upper_limit = False
        self.lower_limit = False
        self.step_output_high = False
        self.step_high_intervals = []
        self.direction_setups = []
        self.direction_setup_intervals = []
        self.rising_edges = []
        self.speed_history = []
        self.pending_direction_setup = None

    def set_target(self, target: int) -> None:
        self.target = target
        self.kick()

    def kick(self) -> None:
        if self.fault is None and self.phase == "idle":
            self.phase = "interstep"
            self.next_event_us = self.now_us + 1

    def stop(self) -> None:
        self.next_event_us = None
        self.phase = "idle"
        self.step_output_high = False

    def _schedule(self, delay_us: int) -> None:
        self.next_event_us = self.now_us + max(2, delay_us)

    def _timer(self) -> None:
        if self.fault is not None:
            self.stop()
            return
        if self.phase == "step_high":
            self.step_output_high = False
            self.step_high_intervals.append((self.rising_edges[-1], self.now_us))
            self.position += 1 if self.direction == 0 else -1
            self.phase = "interstep"
            period = max(
                STEP_PULSE_US + 2,
                int(1_000_000.0 / self.speed),
            )
            self._schedule(period - STEP_PULSE_US)
            return

        error = self.target - self.position
        if error == 0:
            self.speed = 0.0
            self.phase = "idle"
            self.next_event_us = None
            return
        desired_direction = 0 if error > 0 else 1
        if (desired_direction == 0 and self.upper_limit) or (
            desired_direction == 1 and self.lower_limit
        ):
            self.fault = "limit"
            self.stop()
            return
        if self.direction != desired_direction:
            self.speed = START_HZ
            self.direction = desired_direction
            self.direction_setups.append(self.now_us)
            self.pending_direction_setup = self.now_us
            self.phase = "dir_setup"
            self._schedule(DIR_SETUP_US)
            return
        if self.speed < START_HZ:
            self.speed = START_HZ
        braking_hz = math.sqrt(2.0 * ACCEL_HZ_S * abs(error))
        demanded_hz = min(MAX_HZ, braking_hz)
        increment = ACCEL_HZ_S / self.speed
        if self.speed < demanded_hz:
            self.speed = min(demanded_hz, self.speed + increment)
        else:
            self.speed = max(START_HZ, self.speed - increment)
        self.phase = "step_high"
        self.step_output_high = True
        self.rising_edges.append(self.now_us)
        self.speed_history.append(self.speed)
        if self.pending_direction_setup is not None:
            self.direction_setup_intervals.append(
                self.now_us - self.pending_direction_setup
            )
            self.pending_direction_setup = None
        self._schedule(STEP_PULSE_US)

    def run_until(self, end_us: int) -> None:
        while self.next_event_us is not None and self.next_event_us <= end_us:
            self.now_us = self.next_event_us
            if self.phase == "dir_setup":
                self.phase = "idle"
            self._timer()
        self.now_us = end_us


class FirmwareArV2TrajectoryBehaviorTests(unittest.TestCase):
    def test_sine_f2_and_f3_commands_are_bounded_and_slew_limited(self) -> None:
        for mode in (1, 2, 3):
            controller = ArV2Waveform()
            commands = [
                controller.step(
                    0.25 * math.sin(2.0 * math.pi * index / 80.0),
                    2.0,
                    0.3,
                    mode,
                )
                for index in range(500)
            ]
            self.assertLessEqual(max(abs(value) for value in commands), 2.0)
            self.assertLessEqual(
                max(abs(b - a) for a, b in zip(commands, commands[1:])),
                250.0 * 0.005 + 1e-6,
            )

    def test_trajectory_respects_pulse_direction_setup_and_acceleration(self) -> None:
        trajectory = ParametricV2Trajectory()
        trajectory.set_target(240)
        trajectory.run_until(2_000_000)

        self.assertEqual(trajectory.position, 240)
        self.assertTrue(trajectory.step_high_intervals)
        self.assertTrue(all(
            end - start == STEP_PULSE_US
            for start, end in trajectory.step_high_intervals
        ))
        self.assertLessEqual(max(trajectory.speed_history), MAX_HZ)
        for previous, current in zip(
            trajectory.rising_edges, trajectory.rising_edges[1:]
        ):
            self.assertGreaterEqual(current - previous, 1_000_000 / MAX_HZ - 2)

    def test_rapid_reversal_brakes_and_applies_direction_setup(self) -> None:
        trajectory = ParametricV2Trajectory()
        trajectory.set_target(180)
        trajectory.run_until(250_000)
        position_before_reversal = trajectory.position
        trajectory.set_target(-180)
        trajectory.run_until(2_000_000)

        self.assertEqual(trajectory.position, -180)
        self.assertEqual(len(trajectory.direction_setups), 1)
        self.assertTrue(trajectory.direction_setup_intervals)
        self.assertGreaterEqual(
            trajectory.direction_setup_intervals[-1], DIR_SETUP_US
        )
        self.assertNotEqual(position_before_reversal, trajectory.position)

    def test_immediate_stop_disables_future_steps_and_safe_output_state(self) -> None:
        trajectory = ParametricV2Trajectory()
        trajectory.set_target(200)
        trajectory.run_until(20_000)
        steps_at_stop = len(trajectory.rising_edges)
        trajectory.stop()
        trajectory.run_until(200_000)

        self.assertEqual(len(trajectory.rising_edges), steps_at_stop)
        self.assertEqual(trajectory.phase, "idle")
        self.assertIsNone(trajectory.next_event_us)
        self.assertFalse(trajectory.step_output_high)

    def test_limit_and_sensor_faults_stop_trajectory(self) -> None:
        for fault in ("limit", "sensor"):
            trajectory = ParametricV2Trajectory()
            trajectory.set_target(100)
            trajectory.run_until(20_000)
            if fault == "limit":
                trajectory.upper_limit = True
            else:
                trajectory.fault = "sensor"
            trajectory.run_until(100_000)
            self.assertIsNone(trajectory.next_event_us)
            self.assertEqual(trajectory.phase, "idle")
            steps_at_fault = len(trajectory.rising_edges)
            trajectory.run_until(200_000)
            self.assertEqual(len(trajectory.rising_edges), steps_at_fault)

    def test_restart_starts_from_current_commanded_position(self) -> None:
        trajectory = ParametricV2Trajectory()
        trajectory.set_target(100)
        trajectory.run_until(30_000)
        trajectory.stop()
        stopped_position = trajectory.position
        restarted = ParametricV2Trajectory(stopped_position)
        restarted.set_target(stopped_position + 30)
        restarted.run_until(500_000)
        self.assertEqual(restarted.position, stopped_position + 30)
        self.assertEqual(restarted.direction_setups, [])


if __name__ == "__main__":
    unittest.main()
