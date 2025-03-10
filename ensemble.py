import os.path
import sys
import numpy as np
import torch
from torch.utils.data import DataLoader
from torch import tensor

import genotypes
import utils
import logging
import argparse
import torch.nn as nn
import torch.utils
import torchvision.datasets as dset
import torch.backends.cudnn as cudnn

from torch.autograd import Variable
from model import NetworkCIFAR as Network

CIFAR_CLASSES = 10


def main(args):
    if not torch.cuda.is_available():
        logging.info('no gpu device available')
        sys.exit(1)

    np.random.seed(args.seed)
    torch.cuda.set_device(args.gpu)
    cudnn.benchmark = True
    torch.manual_seed(args.seed)
    cudnn.enabled = True
    torch.cuda.manual_seed(args.seed)
    logging.info('gpu device = %d' % args.gpu)
    logging.info("args = %s", args)

    models_folder = args.models_folder
    if not os.path.isdir(models_folder):
        logging.error("The models_folder argument %s is not a directory", models_folder)
        sys.exit(1)

    models = dict()
    for dir in os.listdir(models_folder):
        if dir.startswith("eval"):
            weights_file = os.path.join(models_folder, dir, "weights.pt")
            if os.path.exists(weights_file):
                arch = dir.split("-")[1]
                genotype = genotypes.__dict__.get(arch, None)
                if genotype is not None:
                    model = Network(args.init_channels, CIFAR_CLASSES, args.layers, args.auxiliary, genotype)
                    model = model.cuda()
                    utils.load(model, weights_file)
                    model.drop_path_prob = args.drop_path_prob
                    model.eval()
                    models[arch] = model
                    logging.info("%s param size = %fMB", dir, utils.count_parameters_in_MB(model))
                else:
                    logging.info("Ignoring %s because there is no genotype %s on genotype.py", dir, arch)
    if len(models) == 0:
        logging.error("No model was found on %s", models_folder)
        sys.exit(1)

    criterion = nn.CrossEntropyLoss()
    criterion = criterion.cuda()

    train_transform, test_transform = utils._data_transforms_cifar10(args)
    if args.set == 'cifar100':
        train_data = dset.CIFAR100(root=args.data, train=True, download=True, transform=train_transform)
        valid_data = dset.CIFAR100(root=args.data, train=True, download=True, transform=test_transform)
        test_data = dset.CIFAR100(root=args.data, train=False, download=True, transform=test_transform)
    else:
        train_data = dset.CIFAR10(root=args.data, train=True, download=True, transform=train_transform)
        valid_data = dset.CIFAR10(root=args.data, train=True, download=True, transform=test_transform)
        test_data = dset.CIFAR10(root=args.data, train=False, download=True, transform=test_transform)

    num_train = len(train_data)
    indices = list(range(num_train))
    split = int(np.floor(args.train_portion * num_train))

    train_queue = DataLoader(
        train_data, batch_size=args.batch_size,
        sampler=torch.utils.data.sampler.SubsetRandomSampler(indices[:split]),
        pin_memory=True, num_workers=4)

    valid_queue = torch.utils.data.DataLoader(
        valid_data, batch_size=args.batch_size,
        sampler=torch.utils.data.sampler.SubsetRandomSampler(indices[split:num_train]),
        pin_memory=True, num_workers=4)

    test_queue = DataLoader(
        test_data, batch_size=args.batch_size, shuffle=False, pin_memory=True, num_workers=4)

    if args.calculate:
        # return n_models x n_classes matrix of weights
        if args.per_class:
            weights = calc_ensemble(valid_queue, models, len(valid_data.classes))
        else:
            weights = calc_ensemble(valid_queue, models)
    else:
        weights = torch.ones(len(models), device='cuda')
    logging.info('train final weights = %s', weights)

    # scale weights per maximum value per class
    test_acc, top5_acc, test_obj = infer(test_queue, models, criterion, weights / weights.amax(dim=0))
    logging.info('test loss %e, acc top1: %.2f, acc top5 %.2f', test_obj, test_acc, top5_acc)

    train_acc, top5_acc, train_obj = infer(train_queue, models, criterion)
    logging.info('train loss %e, acc top1: %f, acc top5 %f', train_obj, train_acc, top5_acc)


def calc_ensemble(train_queue, models: dict, n_classes: int = None) -> torch.Tensor:
    weights = []
    # initialize with ones to avoid zero weights
    if n_classes:
        weights.append(torch.ones(len(models), n_classes, device='cuda'))
    with torch.no_grad():
        for step, (input, target) in enumerate(train_queue):
            input = Variable(input).cuda()
            target = Variable(target).cuda()

            logits_list = []
            for model in models.values():
                logits_list.append(model(input)[0])
            # return n_models x n_classes matrix of weights
            if n_classes:
                weights.append(utils.score_per_class(torch.stack(logits_list, dim=1, out=None), target, n_classes))
            else:
                weights.append(utils.score_per_model(torch.stack(logits_list, dim=1, out=None), target))

            if step % (len(train_queue) // args.report_lines) == 0:
                logging.info('train %03d partial weights = %s', step, torch.stack(weights, dim=0).sum(dim=0))

    return torch.stack(weights, dim=0).sum(dim=0)


def infer(test_queue, models: dict, criterion, weights):
    # expand the weight to [1, n_models, n_classes(or 1)] dim then
    if len(weights.size()) == 1:
        weights = weights[None, :, None]
    elif len(weights.size()) == 2:
        weights = weights[None, :]

    objs = utils.AvgrageMeter()
    top1 = utils.AvgrageMeter()
    top5 = utils.AvgrageMeter()
    if weights is None:
        weights = torch.ones(len(models), device='cuda')
    with torch.no_grad():
        for step, (input, target) in enumerate(test_queue):
            input = Variable(input).cuda()
            target = Variable(target).cuda()

            logits_list = []
            loss_list = []
            for model in models.values():
                logits, _ = model(input)
                logits_list.append(logits)
                loss = criterion(logits, target)
                loss_list.append(loss)
            merged_logits = min_max_scaler(torch.stack(logits_list, dim=1, out=None))  # stack the probas and rescale
            # do element-wise multiplication
            weighted_logits = merged_logits * weights
            mean_logits = torch.mean(weighted_logits, dim=1, out=None)
            merged_loss = torch.stack(loss_list, dim=0, out=None)
            merged_loss = torch.mean(merged_loss, dim=0, out=None)
            prec1, prec5 = utils.accuracy(mean_logits, target, topk=(1, 5))
            n = input.size(0)
            objs.update(merged_loss.data.item(), n)
            top1.update(prec1.data.item(), n)
            top5.update(prec5.data.item(), n)

            if step % (len(test_queue) // args.report_lines) == 0:
                logging.info('test %03d %e %f %f', step, objs.avg, top1.avg, top5.avg)

    return top1.avg, top5.avg, objs.avg


def min_max_scaler(input):
    min = torch.amin(input, dim=2, keepdim=True, out=None)  # mix max scaler
    input = torch.add(input, torch.negative(min), out=None)  # add min
    max = torch.amax(input, dim=2, keepdim=True, out=None)  # calculate max
    return torch.div(input, max, out=None)  # devide by max


if __name__ == '__main__':
    parser = argparse.ArgumentParser("ensemble")
    parser.add_argument('--data', type=str, default='data', help='location of the data corpus')
    parser.add_argument('--set', type=str, default='cifar10', help='location of the data corpus')
    parser.add_argument('--batch_size', type=int, default=96, help='batch size')
    parser.add_argument('--report_lines', type=int, default=5, help='number of report lines per stage')
    parser.add_argument('--gpu', type=int, default=0, help='gpu device id')
    parser.add_argument('--init_channels', type=int, default=36, help='num of init channels')
    parser.add_argument('--layers', type=int, default=20, help='total number of layers')
    parser.add_argument('--auxiliary', action='store_true', default=False, help='use auxiliary tower')
    parser.add_argument('--cutout', action='store_true', default=False, help='use cutout')
    parser.add_argument('--cutout_length', type=int, default=16, help='cutout length')
    parser.add_argument('--drop_path_prob', type=float, default=0.2, help='drop path probability')
    parser.add_argument('--seed', type=int, default=0, help='random seed')

    # specific args
    parser.add_argument('--train_portion', type=float, default=0.9, help='portion of training data')
    parser.add_argument('--models_folder', type=str, required=True, help='parent path of pretrained models')
    parser.add_argument('--calculate', action='store_true', default=False,
                        help='Calculate weights for ensemble based on training results')
    parser.add_argument('--per_class', action='store_true', default=False, help='Emsemble per class')
    args = parser.parse_args()

    log_format = '%(asctime)s %(message)s'
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format=log_format, datefmt='%m/%d %H:%M:%S')
    main(args)
