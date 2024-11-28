from isaacgym import gymapi, gymtorch
import torch
import numpy as np

class LunarLanderEnv:
    def __init__(self, num_envs=1, sim_device="cuda:0", sdf_path="lander.urdf"):
        self.num_envs = num_envs
        self.sim_device = sim_device
        self.dt = 1.0 / 60.0
        self.sdf_path = sdf_path

        # Initialize Gym API
        self.gym = gymapi.acquire_gym()

        # Simulation parameters
        sim_params = gymapi.SimParams()
        sim_params.dt = self.dt
        sim_params.use_gpu_pipeline = False

        # Create simulation
        self.sim = self.gym.create_sim(0, 0, gymapi.SIM_PHYSX, sim_params)
        if self.sim is None:
            raise Exception("Failed to create simulation")

        # Create environments
        self.envs = []
        self.landers = []
        self._create_envs()

        # Observation and action spaces
        self.observation_space = np.zeros(8, dtype=np.float32)
        self.action_space = np.array([-1, 0, 1], dtype=np.int32)

        # Viewer
        self.viewer = self.gym.create_viewer(self.sim, gymapi.CameraProperties())
        if self.viewer is None:
            raise Exception("Failed to create viewer")
        
        # Set the camera position for the viewer
        cam_pos = gymapi.Vec3(0.0, 15.0, 25.0)  # Adjust the values as needed
        cam_target = gymapi.Vec3(0.0, 10.0, 0.0)  # Point towards your lander

        self.gym.viewer_camera_look_at(self.viewer, None, cam_pos, cam_target)

    def _create_envs(self):
        spacing = 10.0
        lower = gymapi.Vec3(-spacing, 0.0, -spacing)
        upper = gymapi.Vec3(spacing, spacing, spacing)

        # Load the SDF model
        asset_root = "."
        asset_file = self.sdf_path
        asset_options = gymapi.AssetOptions()
        asset_options.fix_base_link = False

        lander_asset = self.gym.load_asset(self.sim, asset_root, asset_file, asset_options)
        if lander_asset is None:
            raise Exception(f"Failed to load asset from {asset_file}")

        # Create environments and place the lander in each
        for i in range(self.num_envs):
            env = self.gym.create_env(self.sim, lower, upper, 1)
            self.envs.append(env)

            start_pose = gymapi.Transform()
            start_pose.p = gymapi.Vec3(0.0, 10.0, 0.0)
            lander_handle = self.gym.create_actor(env, lander_asset, start_pose, "lander", i, 1)
            self.landers.append(lander_handle)

    def reset(self):
        self.gym.simulate(self.sim)
        self.gym.fetch_results(self.sim, True)

        # Acquire the root state tensor if it doesn't exist
        if not hasattr(self, "root_state_tensor"):
            self.root_state_tensor = self.gym.acquire_actor_root_state_tensor(self.sim)
            self.root_states = gymtorch.wrap_tensor(self.root_state_tensor)

        # Reset positions and velocities for all environments
        for i in range(self.num_envs):
            self.root_states[i, 0:3] = torch.tensor([0.0, 10.0, 0.0], device=self.sim_device)
            self.root_states[i, 3:7] = torch.tensor([0.0, 0.0, 0.0, 1.0], device=self.sim_device)
            self.root_states[i, 7:10] = 0.0
            self.root_states[i, 10:13] = 0.0

        # Commit the changes to the simulator
        self.gym.set_actor_root_state_tensor(self.sim, self.root_state_tensor)

        return self._get_observation()

    def step(self, actions):
        # Adjust velocities based on actions
        for i, action in enumerate(actions):
            if action == 0:  # Main thrust
                self.root_states[i, 7] += 0.0  # vx
                self.root_states[i, 8] += 0.5  # vy
                self.root_states[i, 9] += 0.0  # vz
            elif action == 1:  # Left thrust
                self.root_states[i, 7] += -0.1
                self.root_states[i, 8] += 0.3
                self.root_states[i, 9] += 0.0
            elif action == 2:  # Right thrust
                self.root_states[i, 7] += 0.1
                self.root_states[i, 8] += 0.3
                self.root_states[i, 9] += 0.0

        # Commit the changes to the simulator
        self.gym.set_actor_root_state_tensor(self.sim, self.root_state_tensor)

        # Step simulation
        self.gym.simulate(self.sim)
        self.gym.fetch_results(self.sim, True)

        # Get observations, rewards, and done flags
        obs = self._get_observation()
        rewards = self._get_rewards()
        dones = self._get_dones()
        return obs, rewards, dones, {}

    def render(self):
        if not self.gym.query_viewer_has_closed(self.viewer):
            self.gym.step_graphics(self.sim)
            self.gym.draw_viewer(self.viewer, self.sim, True)
            self.gym.sync_frame_time(self.sim)
        else:
            self.close()

    def close(self):
        self.gym.destroy_viewer(self.viewer)
        self.gym.destroy_sim(self.sim)

    def _get_observation(self):
        obs = []
        for env, lander in zip(self.envs, self.landers):
            state = self.gym.get_actor_rigid_body_states(env, lander, gymapi.STATE_ALL)

            # Extract structured data fields
            position = np.array([state['pose']['p']['x'][0], state['pose']['p']['y'][0], state['pose']['p']['z'][0]], dtype=np.float32)
            velocity = np.array([state['vel']['linear']['x'][0], state['vel']['linear']['y'][0], state['vel']['linear']['z'][0]], dtype=np.float32)
            angle = np.array([state['pose']['r']['x'][0], state['pose']['r']['y'][0], state['pose']['r']['z'][0], state['pose']['r']['w'][0]], dtype=np.float32)
            angular_velocity = np.array([state['vel']['angular']['x'][0], state['vel']['angular']['y'][0], state['vel']['angular']['z'][0]], dtype=np.float32)

            # Concatenate the arrays
            obs.append(np.concatenate([position, velocity, angle, angular_velocity]))

        return np.array(obs, dtype=np.float32)

    def _get_rewards(self):
        rewards = []
        for env, lander in zip(self.envs, self.landers):
            state = self.gym.get_actor_rigid_body_states(env, lander, gymapi.STATE_ALL)
            position = np.array([state['pose']['p']['x'][0], state['pose']['p']['y'][0], state['pose']['p']['z'][0]], dtype=np.float32)
            reward = -np.linalg.norm(position)  # Negative distance from origin
            rewards.append(reward)
        return np.array(rewards)

    def _get_dones(self):
        dones = []
        for env, lander in zip(self.envs, self.landers):
            state = self.gym.get_actor_rigid_body_states(env, lander, gymapi.STATE_ALL)
            y_position = state['pose']['p']['y'][0]
            done = y_position <= 0.0  # Y position below ground
            dones.append(done)
        return np.array(dones)

# Usage Example
env = LunarLanderEnv(num_envs=4, sdf_path="lander.urdf")
obs = env.reset()

for _ in range(1000):
    actions = np.random.choice([0, 1, 2], size=4)  # Random actions
    obs, rewards, dones, info = env.step(actions)
    env.render()

env.close()