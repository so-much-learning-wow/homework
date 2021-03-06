import os
import copy
import random
import numpy as np
import tensorflow as tf
import gym
from dynamics import NNDynamicsModel
from controllers import MPCcontroller, RandomController
from cost_functions import trajectory_cost_fn
import time
import logz
import matplotlib.pyplot as plt
from cheetah_env import HalfCheetahEnvNew

def sample(env,
           controller,
           num_rollouts=10,
           horizon=1000,
           render=False,
           verbose=False):
    """
        Write a sampler function which takes in an environment, a controller (either random or the MPC controller),
        and returns rollouts by running on the env.
        Each path can have elements for observations, next_observations, rewards, returns, actions, etc.
    """
    paths = []

    for i in range(num_rollouts):
        print('Generating a path {}/{}'.format(i+1, num_rollouts))

        path = {
            'observations': [],
            'actions': [],
            'next_observations': [],
            'rewards': []
        }

        ob, done, t = env.reset(), False, 0

        while t <= horizon and not done:
            ac = controller.get_action(ob)
            next_ob, rew, done, _ = env.step(ac)

            t += 1

            path['observations'].append(ob)
            path['actions'].append(ac)
            path['next_observations'].append(next_ob)
            path['rewards'].append(rew)

            ob = next_ob

        paths.append(path)

    return paths

# Utility to compute cost a path for a given cost function
def path_cost(path):
    return trajectory_cost_fn(path['observations'], path['actions'], path['next_observations'])

def compute_normalization_stats(paths):
    """
    Write a function to take in a dataset and compute the means, and stds.
    Return 6 elements: mean of s_t, std of s_t, mean of (s_t+1 - s_t),
    std of (s_t+1 - s_t), mean of actions, std of actions
    """

    obs = np.concatenate([path['observations'] for path in paths])
    acs = np.concatenate([path['actions'] for path in paths])
    next_obs = np.concatenate([path['next_observations'] for path in paths])
    deltas = next_obs - obs

    return {
        'obs_mean': np.mean(obs, axis=0),
        'obs_std': np.std(obs, axis=0) + np.finfo(np.float).eps,
        'acs_mean': np.mean(acs, axis=0),
        'acs_std': np.std(acs, axis=0) + np.finfo(np.float).eps,
        'deltas_mean': np.mean(deltas, axis=0),
        'deltas_std': np.std(deltas, axis=0) + np.finfo(np.float).eps
    }


def plot_comparison(env, dyn_model):
    """
    Write a function to generate plots comparing the behavior of the model predictions
    for each element of the state to the actual ground truth, using randomly sampled actions.
    """

    """ YOUR CODE HERE """

    pass

def train(env,
         logdir=None,
         render=False,
         learning_rate=1e-3,
         dagger_iters=10,
         dynamics_iters=60,
         batch_size=512,
         num_random_rollouts=10,
         num_onpol_rollouts=10,
         num_simulated_paths=10000,
         env_horizon=1000,
         mpc_horizon=15,
         n_layers=2,
         n_hid_units=500,
         activation=tf.nn.relu,
         output_activation=None
         ):

    """

    Arguments:

    dagger_iters                 Number of iterations of onpolicy aggregation for the loop to run.

    dyn_iters                   Number of iterations of training for the dynamics model
    |_                          which happen per iteration of the aggregation loop.

    batch_size                  Batch size for dynamics training.

    num_random_rollouts            Number of paths/trajectories/rollouts generated
    |                           by a random agent. We use these to train our
    |_                          initial dynamics model.

    num_onpol_rollouts          Number of paths to collect at each iteration of
    |_                          aggregation, using the Model Predictive Control policy.

    num_simulated_paths         How many fictitious rollouts the MPC policy
    |                           should generate each time it is asked for an
    |_                          action.

    env_horizon                 Number of timesteps in each path.

    mpc_horizon                 The MPC policy generates actions by imagining
    |                           fictitious rollouts, and picking the first action
    |                           of the best fictitious rollout. This argument is
    |                           how many timesteps should be in each fictitious
    |_                          rollout.

    n_layers/n_hid_units/activations   Neural network architecture arguments.

    """

    logz.configure_output_dir(logdir)

    #========================================================
    #
    # First, we need a lot of data generated by a random
    # agent, with which we'll begin to train our dynamics
    # model.

    random_controller = RandomController(env)
    paths = sample(env, random_controller, num_rollouts=num_random_rollouts, horizon=env_horizon)

    #========================================================
    #
    # The random data will be used to get statistics (mean
    # and std) for the observations, actions, and deltas
    # (where deltas are o_{t+1} - o_t). These will be used
    # for normalizing inputs and denormalizing outputs
    # from the dynamics network.

    normalization_stats = compute_normalization_stats(paths)


    #========================================================
    #
    # Build dynamics model and MPC controllers.
    #
    sess = tf.Session()

    dyn_model = NNDynamicsModel(env=env,
                                n_layers=n_layers,
                                n_hid_units=n_hid_units,
                                activation=activation,
                                output_activation=output_activation,
                                normalization_stats=normalization_stats,
                                batch_size=batch_size,
                                num_iter=dynamics_iters,
                                learning_rate=learning_rate,
                                sess=sess)

    mpc_controller = MPCcontroller(env=env,
                                   dyn_model=dyn_model,
                                   horizon=mpc_horizon,
                                   num_simulated_paths=num_simulated_paths)


    #========================================================
    #
    # Tensorflow session building.
    #
    sess.__enter__()
    tf.global_variables_initializer().run()

    #========================================================
    #
    # Take multiple iterations of onpolicy aggregation
    # at each iteration refitting the dynamics model to current dataset
    # and then taking on-policy samples and aggregating to the dataset.
    #
    # Note: You don't need to use a mixing ratio in this assignment
    # for new and old data as described in https://arxiv.org/abs/1708.02596
    #
    for i in range(dagger_iters):
        print('********** ITERATION {}/{} ************'.format(i+1, dagger_iters))

        # Fitting dynamics model
        dyn_model.fit(paths)

        # Sampling on-policy
        new_paths = sample(env, mpc_controller, num_rollouts=num_onpol_rollouts, horizon=env_horizon)
        paths = new_paths + random.sample(paths, len(new_paths) // 9) # Adding new paths and forgetting old ones
        # paths += new_paths

        returns = [sum(path['rewards']) for path in new_paths]
        costs = [path_cost(path) for path in new_paths]

        # LOGGING
        # Statistics for performance of MPC policy using our learned dynamics model
        # In terms of cost function which your MPC controller uses to plan
        logz.log_tabular('AverageCost', np.mean(costs))
        logz.log_tabular('StdCost', np.std(costs))
        logz.log_tabular('MinimumCost', np.min(costs))
        logz.log_tabular('MaximumCost', np.max(costs))

        # In terms of true environment reward of your rolled out trajectory using the MPC controller
        logz.log_tabular('AverageReturn', np.mean(returns))
        logz.log_tabular('StdReturn', np.std(returns))
        logz.log_tabular('MinimumReturn', np.min(returns))
        logz.log_tabular('MaximumReturn', np.max(returns))

        logz.dump_tabular()

def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--env_name', type=str, default='HalfCheetah-v1')

    # Experiment meta-params
    parser.add_argument('--exp_name', type=str, default='mb_mpc')
    parser.add_argument('--seed', type=int, default=3)
    parser.add_argument('--render', action='store_true')

    # Training args
    parser.add_argument('--learning_rate', '-lr', type=float, default=1e-3)
    parser.add_argument('--dagger_iters', '-di', type=int, default=10)
    parser.add_argument('--dyn_iters', '-nd', type=int, default=60)
    parser.add_argument('--batch_size', '-b', type=int, default=512)

    # Data collection
    parser.add_argument('--num_random_rollouts', '-nrr', type=int, default=10)
    parser.add_argument('--num_onpol_rollouts', '-nor', type=int, default=10)
    parser.add_argument('--num_simulated_paths', '-nsp', type=int, default=1000)
    parser.add_argument('--ep_len', '-ep', type=int, default=1000)

    # Neural network architecture args
    parser.add_argument('--n_layers', '-l', type=int, default=2)
    parser.add_argument('--n_hid_units', '-nh', type=int, default=512)

    # MPC Controller
    parser.add_argument('--mpc_horizon', '-m', type=int, default=15)
    args = parser.parse_args()

    # Set seed
    np.random.seed(args.seed)
    tf.set_random_seed(args.seed)

    # Make data directory if it does not already exist
    if not(os.path.exists('data')): os.makedirs('data')
    logdir = args.exp_name + '_' + args.env_name + '_' + time.strftime("%d-%m-%Y_%H-%M-%S")
    logdir = os.path.join('data', logdir)
    if not(os.path.exists(logdir)): os.makedirs(logdir)

    # Make env
    if args.env_name is "HalfCheetah-v1":
        env = HalfCheetahEnvNew()

    train(env=env,
        logdir=logdir,
        render=args.render,
        learning_rate=args.learning_rate,
        dagger_iters=args.dagger_iters,
        dynamics_iters=args.dyn_iters,
        batch_size=args.batch_size,
        num_random_rollouts=args.num_random_rollouts,
        num_onpol_rollouts=args.num_onpol_rollouts,
        num_simulated_paths=args.num_simulated_paths,
        env_horizon=args.ep_len,
        mpc_horizon=args.mpc_horizon,
        n_layers = args.n_layers,
        n_hid_units=args.n_hid_units,
        activation=tf.nn.relu,
        output_activation=None,
    )

if __name__ == "__main__":
    main()
