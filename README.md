# MOOD-NAS
One network for each mood.

## Introduction

**MOOD NAS** (MultiObjective Optimization Differentiable Neural Architecture Search) explore the common neglected conflicting objectives on NAS formulation. This way focuses on gradually adding regularization (complexity measure) strength to the model, thus filling an approximation of the Pareto frontier with efficient learning models exhibiting distinct trade-offs between error and model complexity. ~~For a detailed description of technical details and experimental results, please refer to our paper: MultiObjective Differentiable Neural Architecture Search~~ TBD

Authors: [Raphael Adamski](https://www.linkedin.com/in/iksmada/?locale=en_US), [Marcos Medeiros Raimundo](https://marcosmrai.github.io/), [Fernando Jose Von Zuben](https://www.dca.fee.unicamp.br/~vonzuben/).

**This code is based on the implementation of [PC-DARTS](https://github.com/yuhuixu1993/PC-DARTS) which, in turn, is based on [DARTS](https://github.com/quark0/darts).**

## Results
### Results on CIFAR10

### L2 loss vs Cross-entropy loss
![L2 loss vs Cross-entropy loss Pareto-frontier](image/l1_0_l2_vary.png?raw=true)

### L1 loss vs Cross-entropy loss
![L1 loss vs Cross-entropy loss Pareto-frontier](image/l1_vary_l2_fixed.png?raw=true)

Smallest weights on above Pareto-frontier:

| Weight      | Params(M) | Error(%) |
|-------------|:---------:|:--------:|
| &nu; = 3e-6 |    3.1    |   3.15   |  
| &nu; = 4e-6 |    4.0    |   3.18   |  
| &nu; = 7e-6 |    3.3    |   3.14   |  
| &nu; = 1e-4 |    4.3    |   2.98   |  
| &nu; = 2e-4 |    4.0    |   3.82   |  
| Ensemble    |   14.6    | **2.58** |  
| AmoebaNet-B |    2.8    |   2.55   |
| DARTSV1     |    3.3    |   3.00   |  
| DARTSV2     |    3.3    |   2.76   |  
| SNAS        |    2.8    |   2.85   | 
| PC-DARTS    |    3.6    |   2.57   |  


## Usage
### Search on CIFAR10

To run the code, it is suggested a 12G memory GPU, but it can work with smaller sizes but slower (reduce the `batch_size` arg).

Framework flow:
![Framework flow](image/framework.png?raw=true)

#### Create the Pareto frontier
L2 loss
```
python multiobjective.py -o l2 --weight_decay 0.0
```
L1 loss
```
python multiobjective.py -o l1 --weight_decay 0.0
```
L1 loss with fixed value of l2
```
python multiobjective.py -o l1 --weight_decay 3e-4
```
Other parameters are compatible with `python train_search.py` original arguments.

#### Make the code selection

Run the following tool to give you more information about the samples found during above search process like latency, FLOPs, params and useful plots.
``` 
export PYTHONPATH=$PYTHONPATH:..; \\
python tools/analyse_search_logs.py -l log.txt
```
The `log.txt` is inside output folder of `multiobjective.py` run.

#### Evaluate arch code in batch

The evaluation process simply follows PC-DARTS configuration. Moreover, we created a script to predict GPU memory consumption automatically to make training various codes (with different size and `batch_size`) easily. More information about this in the appendix.

``` 
python batch_train.py --archs CODE [CODE ...] --train_portion 1.0\\
       --auxiliary \\
       --cutout \\
```

The `CODE` should be a variable name on genotypes.py file (e.g. `PC_DARTS_cifar` or `l2_loss_2e01`). Other parameters are compatible with `python train.py` original arguments.
The `--train_portion` controls the train/validation portion. 0.9 for instance use 90% of data for training ans 10% for validation.

#### Make the model selection

Run the following tool to give you more information about the models evaluated during above training process like latency, FLOPs, params and useful plots.
``` 
export PYTHONPATH=$PYTHONPATH:..; \\
python tools/analyse_train_logs.py -s search_log [search_log ...] -t train_log [train_log ...]
```
The `search_log` is inside output folder of `multiobjective.py` run.
The `train_log` is inside each output folder of `batch_train.py` runs (each model evaluation create an evaluation log folder).

#### Ensemble generation
To create an ensemble from trained model run the following script:
``` 
python ensemble.py --models_folder log_folder \\
       --calculate \\
       --auxiliary \\
       --cutout \\
```

The `log_folder` should be a path to a folder containing evaluation log sub folders. `--calculate` with calculate the weight of the models given the training set metrics. Other parameters are compatible with `python train.py` original arguments.

## Notes
- All scripts from PC-DARTS were update and are now compatible with `python3+` and `pytorch1.8.1+`. We ran all expriments on a `Tesla V100` GPU.
- All Pareto optimal codes (Genotypes) found on the search stage (using all three cases) are available at `genotypes.py` file.

## Related work

[Partial Channel Connections for Memory-Efficient Differentiable Architecture Search](https://github.com/yuhuixu1993/PC-DARTS)

[Differentiable Architecture Search](https://github.com/quark0/darts)

## Reference

If you use our code in your research, please cite our paper accordingly.

TDB

## Appendix

### GPU memory estimator

In order to create a naive estimator to memory consumption and avoid the cumbersome work to tune the `batch_size` for each architecture under evaluation, we create a [dataset](batch_data.csv?raw=true) and a simple predictor using Polynomial Ridge regression.

#### How to use

```python
import pickle
# load mode, sklearn is necessary
batch_model = pickle.load(open("batch_predict.pkl", "rb")) 

batch_size = 200 # polite guess
params_in_millions=3.63 # PC-DARTS size
# predict memory consumption given the number of params and batch_size
consumption = batch_model.predict([[params_in_millions, batch_size]])
```

#### How to train a new model

``` 
python batch_size_predict.py --data csv_file
```
where the `csv_file` should has the same format of the [batch_data.csv](batch_data.csv?raw=true) with columns 'model size' 'batch size' and 'GPU mb'
