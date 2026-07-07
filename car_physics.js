// car_physics.js
// Self-contained ES module implementing a simple car physics model for Three.js games.
// The model includes:
//   • Suspension per wheel (spring & damper)
//   • Tire force based on a simplified Pacejka "Magic Formula" (slip ratio -> grip)
//   • Engine torque curve and gear ratios
//   • Weight transfer during acceleration and braking
//   • Ackermann steering geometry
//
// The class works with a fixed timestep. Call `update(dt)` each frame (dt in seconds).
// It does NOT perform any rendering; you can query the resulting forces/torques and
// apply them to a Three.js mesh representing the vehicle.

export class CarPhysics {
  /**
   * Create a new CarPhysics instance.
   * @param {Object} options Configuration options.
   * @param {number} options.mass Vehicle mass (kg).
   * @param {number} options.wheelBase Distance between front and rear axles (m).
   * @param {number} options.trackWidth Distance between left and right wheels (m).
   * @param {number} options.cgHeight Height of centre of gravity above ground (m).
   * @param {Array<Object>} options.wheels Array of 4 wheel definitions (front left, front right, rear left, rear right).
   *   Each wheel definition can contain:
   *     - position: {x, y, z} position of suspension anchor in vehicle space (typically at hub).
   *     - restLength: suspension rest length (m).
   *     - springRate: suspension spring stiffness (N/m).
   *     - damperRate: damper coefficient (N·s/m).
   *     - radius: tire radius (m).
   * @param {Array<number>} options.gearRatios Gear ratios, e.g. [-3.5, 0, 3.2, 2.1, 1.5, 1.0] (reverse, neutral, 1st,...).
   * @param {Array<number>} options.finalDrive Final drive ratio.
   * @param {Array<Object>} options.torqueCurve Engine torque curve points [{rpm, torque}].
   * @param {number} options.maxSteerAngle Maximum steering angle (rad).
   * @param {number} options.ackermannFactor Factor for Ackermann geometry (1 = perfect Ackermann).
   */
  constructor(options = {}) {
    // Basic vehicle parameters
    this.mass = options.mass ?? 1500; // kg
    this.wheelBase = options.wheelBase ?? 2.6; // m
    this.trackWidth = options.trackWidth ?? 1.5; // m
    this.cgHeight = options.cgHeight ?? 0.5; // m

    // Wheels (must be 4)
    const defaultWheel = {
      position: { x: 0, y: 0, z: 0 },
      restLength: 0.3,
      springRate: 30000,
      damperRate: 4500,
      radius: 0.34,
    };
    this.wheels = (options.wheels ?? Array(4).fill(defaultWheel)).map((w) => ({ ...defaultWheel, ...w }));

    // Suspension state per wheel
    this.suspensionLength = this.wheels.map((w) => w.restLength);
    this.suspensionVelocity = this.wheels.map(() => 0);

    // Transmission
    this.gearRatios = options.gearRatios ?? [0, 3.2, 2.1, 1.5, 1.0]; // index 0 = neutral
    this.finalDrive = options.finalDrive ?? 3.42;
    this.currentGear = 1; // start in first gear
    this.engineRpm = 800; // idle rpm
    this.torqueCurve = options.torqueCurve ?? [
      { rpm: 800, torque: 150 },
      { rpm: 2000, torque: 250 },
      { rpm: 4000, torque: 300 },
      { rpm: 6000, torque: 250 },
    ];
    this.maxSteerAngle = options.maxSteerAngle ?? Math.PI / 4; // 45 deg
    this.ackermannFactor = options.ackermannFactor ?? 1; // 1 = perfect Ackermann

    // State variables
    this.velocity = { x: 0, y: 0, z: 0 }; // vehicle linear velocity in world space
    this.angularVelocity = { x: 0, y: 0, z: 0 }; // vehicle angular velocity (approx yaw only)
    this.steerInput = 0; // -1..1
    this.throttle = 0; // 0..1
    this.brake = 0; // 0..1

    // Pre‑compute wheel lateral offsets for Ackermann steering
    this.wheelOffsets = this._computeWheelOffsets();
  }

  /** Compute lateral offsets for the four wheels. Returns an array of vectors */
  _computeWheelOffsets() {
    // Assuming order: FL, FR, RL, RR
    const halfTrack = this.trackWidth / 2;
    const halfWB = this.wheelBase / 2;
    return [
      { x: -halfWB, y: 0, z: halfTrack }, // front left
      { x: -halfWB, y: 0, z: -halfTrack }, // front right
      { x: halfWB, y: 0, z: halfTrack }, // rear left
      { x: halfWB, y: 0, z: -halfTrack }, // rear right
    ];
  }

  /** Interpolate torque from the torque curve */
  _engineTorque(rpm) {
    const curve = this.torqueCurve;
    if (curve.length === 0) return 0;
    // clamp
    if (rpm <= curve[0].rpm) return curve[0].torque;
    if (rpm >= curve[curve.length - 1].rpm) return curve[curve.length - 1].torque;
    // linear interpolation between points
    for (let i = 0; i < curve.length - 1; i++) {
      const p1 = curve[i];
      const p2 = curve[i + 1];
      if (rpm >= p1.rpm && rpm <= p2.rpm) {
        const t = (rpm - p1.rpm) / (p2.rpm - p1.rpm);
        return p1.torque + t * (p2.torque - p1.torque);
      }
    }
    return 0;
  }

  /** Compute longitudinal slip ratio for a driven wheel */
  _slipRatio(wheelSpeed, vehicleSpeed) {
    // wheelSpeed = rad/s * radius => linear speed at tire
    if (Math.abs(vehicleSpeed) < 0.1) return 0; // avoid division by zero
    return (wheelSpeed - vehicleSpeed) / Math.abs(vehicleSpeed);
  }

  /** Simplified Pacejka "Magic Formula" for tire forces.
   *   F = D * sin(C * atan(B * slip))
   *   where B, C, D are shape parameters. We'll use typical values.
   */
  _pacejkaForce(slip, isLongitudinal = true) {
    const B = isLongitudinal ? 10 : 5; // stiffness
    const C = 1.9; // shape
    const D = isLongitudinal ? 1 : 1; // peak (normalized to normal load)
    // Normalised force (0..1). Multiply by normal load later.
    return D * Math.sin(C * Math.atan(B * slip));
  }

  /** Update vehicle state for a fixed timestep dt (seconds) */
  update(dt) {
    // 1. Engine torque & drive force
    const gearRatio = this.gearRatios[this.currentGear] * this.finalDrive;
    const engineTorque = this._engineTorque(this.engineRpm) * this.throttle;
    const driveTorque = engineTorque * gearRatio * this.throttle; // simplified * throttle
    // Convert to wheel force (torque / radius)
    const wheelForce = driveTorque / this.wheels[0].radius; // assume same radius for all

    // 2. Weight transfer (simplified longitudinal)
    const g = 9.81;
    const longitudinalAccel = (wheelForce / this.mass) - (this.brake * g);
    const weightTransfer = (this.mass * longitudinalAccel * this.cgHeight) / this.wheelBase;
    // Distribute to front/rear wheels
    const frontWeight = (this.mass * g / 2) - weightTransfer / 2;
    const rearWeight = (this.mass * g / 2) + weightTransfer / 2;
    const wheelLoads = [frontWeight / 2, frontWeight / 2, rearWeight / 2, rearWeight / 2]; // FL, FR, RL, RR

    // 3. Tire forces per wheel (simplified longitudinal only)
    const vehicleSpeed = this.velocity.z; // assume forward is +z in vehicle space
    const wheelSpeeds = this.wheels.map((w) => {
      // Approximate wheel angular speed from vehicle speed (no slip yet)
      return vehicleSpeed / w.radius;
    });

    const longitudinalForces = [];
    for (let i = 0; i < 4; i++) {
      const slip = this._slipRatio(wheelSpeeds[i], vehicleSpeed);
      const slipForceCoeff = this._pacejkaForce(slip, true);
      const normalLoad = wheelLoads[i];
      const force = slipForceCoeff * normalLoad;
      longitudinalForces.push(force);
    }

    // Apply forces to vehicle acceleration (sum longitudinal forces)
    const totalLongForce = longitudinalForces.reduce((a, b) => a + b, 0) - this.brake * this.mass * g;
    const accel = totalLongForce / this.mass;
    // Update linear velocity (only forward direction for simplicity)
    this.velocity.z += accel * dt;

    // 4. Update suspension spring/damper (simplified vertical dynamics)
    for (let i = 0; i < 4; i++) {
      const wheel = this.wheels[i];
      const load = wheelLoads[i];
      const springForce = wheel.springRate * (wheel.restLength - this.suspensionLength[i]);
      const damperForce = wheel.damperRate * (-this.suspensionVelocity[i]);
      const totalForce = springForce + damperForce - load; // positive upward
      // Simple vertical acceleration (mass of wheel ignored)
      const a = totalForce / this.mass;
      this.suspensionVelocity[i] += a * dt;
      this.suspensionLength[i] += this.suspensionVelocity[i] * dt;
    }

    // 5. Steering – compute wheel angles using Ackermann geometry
    const steerAngle = this.maxSteerAngle * this.steerInput; // desired steering wheel angle
    const halfTrack = this.trackWidth / 2;
    const wheelBase = this.wheelBase;
    // Left front wheel angle
    const leftAngle = Math.atan2(wheelBase, halfTrack / Math.tan(steerAngle) - halfTrack) * this.ackermannFactor;
    // Right front wheel angle (mirror)
    const rightAngle = -Math.atan2(wheelBase, halfTrack / Math.tan(-steerAngle) - halfTrack) * this.ackermannFactor;
    // Store for external use
    this.steeringAngles = { left: leftAngle, right: rightAngle };

    // 6. Simple yaw dynamics – using steering angles and speed
    const avgSteer = (leftAngle + rightAngle) / 2;
    const yawRate = (this.velocity.z / this.wheelBase) * Math.tan(avgSteer);
    this.angularVelocity.y = yawRate;
    // Update orientation (only yaw). We keep a simple yaw angle for convenience.
    if (!this.yaw) this.yaw = 0;
    this.yaw += yawRate * dt;

    // 7. Update engine RPM based on wheel speed and gear ratio
    const rearWheelSpeed = this.velocity.z / this.wheels[2].radius; // rear wheel approx
    const wheelRpm = rearWheelSpeed * 60 / (2 * Math.PI);
    this.engineRpm = wheelRpm * gearRatio;
    if (this.engineRpm < 800) this.engineRpm = 800; // idle
  }

  /** Set driver inputs (range -1..1 for steer, 0..1 for throttle/brake) */
  setInputs({ steer = 0, throttle = 0, brake = 0 }) {
    this.steerInput = Math.max(-1, Math.min(1, steer));
    this.throttle = Math.max(0, Math.min(1, throttle));
    this.brake = Math.max(0, Math.min(1, brake));
  }

  /** Shift gear – index into gearRatios array */
  shiftGear(index) {
    if (index >= 0 && index < this.gearRatios.length) this.currentGear = index;
  }

  /** Get current state useful for rendering */
  getState() {
    return {
      position: { x: 0, y: 0, z: 0 }, // placeholder – integration with transform handled by caller
      velocity: { ...this.velocity },
      yaw: this.yaw ?? 0,
      steeringAngles: this.steeringAngles ?? { left: 0, right: 0 },
      engineRpm: this.engineRpm,
      gear: this.currentGear,
    };
  }
}

// Example usage (commented out – remove comments to test):
/*
import { CarPhysics } from './car_physics.js';
const car = new CarPhysics({ mass: 1500 });
car.setInputs({ steer: 0.2, throttle: 0.7, brake: 0 });
car.update(1/60);
console.log(car.getState());
*/
