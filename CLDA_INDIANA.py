from __future__ import print_function
import argparse
import math
import os
import random

import cleanlab
from sklearn import svm
import torch
import torch.optim as optim
import utils
import basenet
import torch.nn.functional as F
import numpy as np
import warnings
from datapre import  all_data, cubeData,train_test_preclass
from torch import nn
from torch.autograd import Variable
from torch.utils.data import TensorDataset, DataLoader
from sklearn import metrics

warnings.filterwarnings('ignore')

parser = argparse.ArgumentParser(description='CLDA HSI Classification')
parser.add_argument('--batch-size', type=int, default=36, metavar='N',
                    help='input batch size for training (default: 36)')
parser.add_argument('--epochs', type=int, default=100, metavar='N',
                    help='number of epochs to train (default: 10)')
parser.add_argument('--lr', type=float, default=0.01, metavar='LR',
                    help='learning rate (default: 0.0003)')
parser.add_argument('--momentum', type=float, default=0.9, metavar='M',
                    help='SGD momentum (default: 0.5)')
parser.add_argument('--no-cuda', action='store_true', default=False,
                    help='disables CUDA training')
parser.add_argument('--num_k', type=int, default=6, metavar='K',
                    help='how many steps to repeat the generator update')
parser.add_argument('--gpu', type=int, default=0)

args = parser.parse_args()
args.cuda = not args.no_cuda and torch.cuda.is_available()
torch.cuda.set_device(args.gpu)

def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

num_epoch = args.epochs
num_k = args.num_k
BATCH_SIZE = args.batch_size

HalfWidth = 2
n_outputs = 128
nBand = 220
patch_size = 2 * HalfWidth + 1
CLASS_NUM = 7

#load data
file_path = './datasets/Indiana/DataCube.mat'
source_data,target_data,source_label, target_label = cubeData(file_path)

print(source_data.shape,source_label.shape)
print(target_data.shape,target_label.shape)

nDataSet = 1#sample times

acc = np.zeros([nDataSet, 1])
A = np.zeros([nDataSet, CLASS_NUM])
k = np.zeros([nDataSet, 1])

seeds  = [1220,1230,1231,1236,1237,1330,1331,1333,1337,1336]

best_predict_all = 0
best_test_acc = 0
train_loss_ndataset = []
best_G,best_RandPerm,best_Row,best_Column = None,None,None,None

class EmbeddingNetHyperX(nn.Module):
    def __init__(self, input_channels, n_outputs=128, patch_size=5, n_classes=None):
        super(EmbeddingNetHyperX, self).__init__()
        self.dim = 200

        # 1st conv layer
        # input [input_channels x patch_size x patch_size]
        self.convnet = nn.Sequential(
            nn.Conv2d(input_channels, self.dim, kernel_size=1, padding=0),  # input channels
            nn.BatchNorm2d(self.dim, momentum=1,affine=True,track_running_stats=True),
            nn.ReLU(),
            nn.Conv2d(self.dim, self.dim, kernel_size=1, padding=0),
            nn.BatchNorm2d(self.dim, momentum=1,affine=True,track_running_stats=True),
            nn.ReLU(),
            nn.Conv2d(self.dim, self.dim, kernel_size=1, padding=0),
            nn.BatchNorm2d(self.dim,  momentum=1,affine=True,track_running_stats=True),
            nn.ReLU(),
            nn.Conv2d(self.dim, self.dim, kernel_size=1, padding=0,),
            nn.BatchNorm2d(self.dim, momentum=1, affine=True,track_running_stats=True),
            nn.ReLU(),

            nn.AvgPool2d(patch_size, stride=1)

        )

        self.n_outputs = n_outputs
        self.fc = nn.Linear(self.dim, self.n_outputs)

    def extract_features(self, x):

        output = self.convnet(x)
        output = output.view(output.size()[0], -1)
        output = self.fc[0](output)

        return output

    def forward(self, x):

        output = self.convnet(x)
        output = output.view(output.size()[0], -1)
        output = self.fc(output)

        return output

    def get_embedding(self, x):
        return self.forward(x)
    def output_num(self):
        return self.n_outputs


def train(ep, data_loader, data_loader_t,train_epoch,weight_clean):

    criterion_s = nn.CrossEntropyLoss().cuda()
    criterion_t = nn.CrossEntropyLoss(weight=weight_clean).cuda()


    gamma = 0.01
    alpha = 0.1
    beta = 0.01

    for batch_idx, data in enumerate(zip(data_loader, data_loader_t)):
        G.train()
        F1.train()
        F2.train()

        if ep >= train_epoch:
            (data_s, label_s), (data_t, fake_label_t) = data
            fake_label_t = Variable(fake_label_t).cuda()
        else:
            (data_s, label_s), (data_t, _) = data
        if args.cuda:
            data_s, label_s = data_s.cuda(), label_s.cuda()
            data_t = data_t.cuda()

        data_all = Variable(torch.cat((data_s, data_t), 0))
        label_s = Variable(label_s)
        bs = len(label_s)

        """source domain discriminative"""
        # Step A train all networks to minimize loss on source
        output = G(data_all)

        output1 = F1(output)
        output2 = F2(output)
        output_s1 = output1[:bs, :]
        output_s2 = output2[:bs, :]
        output_t1 = output1[bs:, :]
        output_t2 = output2[bs:, :]
        output_t1_prob = F.softmax(output_t1)
        output_t2_prob = F.softmax(output_t2)

        entropy_loss = - torch.mean(torch.log(torch.mean(output_t1_prob, 0) + 1e-6))
        entropy_loss -= torch.mean(torch.log(torch.mean(output_t2_prob, 0) + 1e-6))

        loss1 = criterion_s(output_s1, label_s)
        loss2 = criterion_s(output_s2, label_s)
        if ep >= train_epoch:
            target_loss = criterion_t(output_t1, fake_label_t) + criterion_t(output_t2, fake_label_t)
            entroy_target_loss = utils.EntropyLoss(output_t1_prob) + utils.EntropyLoss(output_t2_prob)

        else:
            target_loss = 0
            entroy_target_loss= 0

        all_loss = loss1 + loss2 + alpha * target_loss + beta * entroy_target_loss + 0.01 * entropy_loss

        optimizer_g.zero_grad()
        optimizer_f.zero_grad()
        all_loss.backward()
        optimizer_g.step()
        optimizer_f.step()

        """target domain discriminative"""
        # Step B train classifier to maximize discrepancy
        output = G(data_all)

        output1 = F1(output)
        output2 = F2(output)
        output_s1 = output1[:bs, :]
        output_s2 = output2[:bs, :]
        output_t1 = output1[bs:, :]
        output_t2 = output2[bs:, :]
        output_t1 = F.softmax(output_t1)
        output_t2 = F.softmax(output_t2)

        loss1 = criterion_s(output_s1, label_s)
        loss2 = criterion_s(output_s2, label_s)
        entropy_loss = - torch.mean(torch.log(torch.mean(output_t1, 0) + 1e-6))
        entropy_loss -= torch.mean(torch.log(torch.mean(output_t2, 0) + 1e-6))
        loss_dis = utils.cdd(output_t1, output_t2)

        F_loss = loss1 + loss2 - gamma * loss_dis + 0.01 * entropy_loss

        optimizer_f.zero_grad()
        F_loss.backward()
        optimizer_f.step()

        # Step C train genrator to minimize discrepancy
        for i in range(num_k):

            output = G(data_all)
            output1 = F1(output)
            output2 = F2(output)
            output_t1 = output1[bs:, :]
            output_t2 = output2[bs:, :]
            output_t1_prob = F.softmax(output_t1)
            output_t2_prob = F.softmax(output_t2)
            entropy_loss = - torch.mean(torch.log(torch.mean(output_t1_prob, 0) + 1e-6))
            entropy_loss -= torch.mean(torch.log(torch.mean(output_t2_prob, 0) + 1e-6))
            loss_dis = utils.cdd(output_t1_prob, output_t2_prob)
            D_loss = gamma * loss_dis + 0.01 * entropy_loss
            optimizer_g.zero_grad()
            D_loss.backward()
            optimizer_g.step()

    print(
        'Train Ep: {} \ttrian_target_dataset:{}\tLoss1: {:.6f}\tLoss2: {:.6f}\t Dis: {:.6f} Entropy: {:.6f} '.format(
            ep, len(data_loader_t.dataset),
            loss1.item(), loss2.item(), loss_dis.item(), entropy_loss.item()))


def get_probs(data_loader,data_loader_t):
    train_features, train_labels = utils.extract_embeddings(G, data_loader)
    clt = svm.SVC(probability=True)
    clt.fit(train_features, train_labels)
    test_features, _ = utils.extract_embeddings(G, data_loader_t)
    probs = clt.predict_proba(test_features)
    return probs

def clean_sampling_epoch(labels, probabilities):

    labels = np.array(labels)
    probabilities = np.array(probabilities)

    # find the error samples index
    label_error_mask = np.zeros(len(labels), dtype=bool)
    label_error_indices = cleanlab.count.compute_confident_joint(
        labels, probabilities, return_indices_of_off_diagonals=True
    )[1]
    for idx in label_error_indices:
        label_error_mask[idx] = True

    label_errors_bool = cleanlab.filter.find_label_issues(labels, probabilities, n_jobs=1)
    ordered_label_errors = cleanlab.rank.order_label_issues(labels=labels,label_issues_mask=label_errors_bool,pred_probs=probabilities)
    true_labels_idx = []
    all_labels_idx = []
    for i in range(len(labels)):
        all_labels_idx.append(i)

    if len(ordered_label_errors) == 0:
        true_labels_idx = all_labels_idx
    else:
        for j in range(len(ordered_label_errors)):
            all_labels_idx.remove(ordered_label_errors[j])
            true_labels_idx = all_labels_idx
    np.random.shuffle(true_labels_idx)
    orig_class_count = np.bincount(labels,minlength = CLASS_NUM)
    train_bool_mask = ~label_errors_bool

    imgs = [labels[i] for i in range(len(labels)) if train_bool_mask[i] ]
    clean_class_counts = np.bincount(imgs,minlength = CLASS_NUM)

    # compute the class weights to re-weight loss during training
    class_weights = torch.Tensor(orig_class_count / clean_class_counts).cuda()

    target_datas = []
    target_labels = []
    for i in range(len(true_labels_idx)):
        target_datas.append(testX[true_labels_idx[i]])
        target_labels.append(labels[true_labels_idx[i]])
    target_datas = np.array(target_datas)
    target_labels = np.array(target_labels)

    return target_datas, target_labels, class_weights

def test(data_loader):
    test_pred_all = []
    test_all = []
    predict = np.array([], dtype=np.int64)

    G.eval()
    F1.eval()
    F2.eval()
    test_loss = 0
    correct_add = 0
    size = 0

    for batch_idx, data in enumerate(data_loader):
        img, label = data
        img, label = img.cuda(), label.cuda()
        img, label = Variable(img, volatile=True), Variable(label)
        output = G(img)
        output1 = F1(output)
        output2 = F2(output)

        output_add = output1 + output2
        pred = output_add.data.max(1)[1]
        test_loss += F.nll_loss(F.log_softmax(output1, dim=1), label, size_average=False).item()
        correct_add += pred.eq(label.data).cpu().sum()
        size += label.data.size()[0]
        test_all = np.concatenate([test_all, label.data.cpu().numpy()])
        test_pred_all = np.concatenate([test_pred_all, pred.cpu().numpy()])
        predict = np.append(predict, pred.cpu().numpy())
    test_accuracy = 100. * float(correct_add) / size
    test_loss /= len(data_loader.dataset)  # loss function already averages over batch size
    print('Test set:test loss:{:.6f}, Accuracy: {}/{} ({:.6f}%)'.format(
         test_loss, correct_add, size, 100. * float(correct_add) / size))

    acc[iDataSet] = test_accuracy
    OA = acc
    C = metrics.confusion_matrix(test_all, test_pred_all)
    A[iDataSet, :] = np.diag(C) / np.sum(C, 1, dtype=np.float)

    k[iDataSet] = metrics.cohen_kappa_score(test_all, test_pred_all)

    return test_accuracy, predict



for iDataSet in range(nDataSet):
    print('#######################idataset######################## ', iDataSet)
    np.random.seed(seeds[iDataSet])
    # set_seed(seeds[iDataSet])
    # data
    train_xs, train_ys = train_test_preclass(source_data, source_label, HalfWidth, 200)
    testX, testY, G_test, RandPerm, Row, Column = all_data(target_data, target_label, HalfWidth)  # (7826,5,5,72)

    train_dataset = TensorDataset(torch.tensor(train_xs), torch.tensor(train_ys))
    test_dataset = TensorDataset(torch.tensor(testX), torch.tensor(testY))

    train_loader_s = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, drop_last=True)
    train_loader_t = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=True, drop_last=True)
    test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False)

    # model
    G = EmbeddingNetHyperX(nBand, n_outputs=n_outputs, patch_size=patch_size, n_classes=CLASS_NUM).cuda()
    F1 = basenet.ResClassifier(num_classes=CLASS_NUM, num_unit=G.output_num(), middle=64)
    F2 = basenet.ResClassifier(num_classes=CLASS_NUM, num_unit=G.output_num(), middle=64)

    if args.cuda:
        G.cuda()
        F1.cuda()
        F2.cuda()
    # optimizer and losss
    optimizer_g = optim.SGD(G.parameters(), lr=args.lr ,weight_decay=0.0005)

    optimizer_f = optim.SGD(list(F1.parameters()) + list(F2.parameters()),momentum=0.9 , lr=args.lr  ,
                            weight_decay=0.0005)
    num_epoch = 100
    train_num = 20
    class_weights = None
    train_loss = []

    for ep in range(1,num_epoch+1):
        if (ep >= train_num and ep < num_epoch) and ep % 20 == 0 :

            print('get  fake label,ep = ',ep)

            # fake_label = utils.obtain_label(test_loader, G, F1, F2)
            _, fake_label = test(test_loader)

            label_list = list(set(fake_label))
            print(label_list)
            if len(label_list) != CLASS_NUM:
                break

            print('get probs,ep=',ep)
            probs = get_probs(train_loader_s, test_loader)
            clean_datas, clean_labels, class_weights = clean_sampling_epoch(fake_label, probs)
            target_datasets = TensorDataset(torch.tensor(clean_datas), torch.tensor(clean_labels))
            train_loader_t = DataLoader(target_datasets, batch_size=BATCH_SIZE, shuffle=True, num_workers=0,drop_last=True)

        train(ep, train_loader_s, train_loader_t, train_num, class_weights)

    print('-' * 100, '\nTesting')
    test_accuracy, predict = test(test_loader)

    if test_accuracy >= best_test_acc:
        best_test_acc = test_accuracy
        best_predict_all = predict
        best_G, best_RandPerm, best_Row, best_Column = G_test, RandPerm, Row, Column

print(train_loss_ndataset)
print(acc)
AA = np.mean(A, 1)
AAMean = np.mean(AA, 0)
AAStd = np.std(AA)
AMean = np.mean(A, 0)
AStd = np.std(A, 0)
OAMean = np.mean(acc)
OAStd = np.std(acc)
kMean = np.mean(k)
kStd = np.std(k)

print("average OA: " + "{:.2f}".format(OAMean) + " +- " + "{:.2f}".format(OAStd))
print("average AA: " + "{:.2f}".format(100 * AAMean) + " +- " + "{:.2f}".format(100 * AAStd))
print("average kappa: " + "{:.4f}".format(100 * kMean) + " +- " + "{:.4f}".format(100 * kStd))
print("accuracy for each class: ")
for i in range(CLASS_NUM):
    print("Class " + str(i) + ": " + "{:.2f}".format(100 * AMean[i]) + " +- " + "{:.2f}".format(100 * AStd[i]))


print('classification map!!!!!')
for i in range(len(best_predict_all)):
    best_G[best_Row[best_RandPerm[ i]]][best_Column[best_RandPerm[ i]]] = best_predict_all[i] + 1

###################################################
hsi_pic = np.zeros((best_G.shape[0], best_G.shape[1], 3))
for i in range(best_G.shape[0]):
    for j in range(best_G.shape[1]):
        if best_G[i][j] == 0:
            hsi_pic[i, j, :] = [0, 0, 0]
        if best_G[i][j] == 1:
            hsi_pic[i, j, :] = [0, 0, 1]
        if best_G[i][j] == 2:
            hsi_pic[i, j, :] = [0, 1, 0]
        if best_G[i][j] == 3:
            hsi_pic[i, j, :] = [0, 1, 1]
        if best_G[i][j] == 4:
            hsi_pic[i, j, :] = [1, 0, 0]
        if best_G[i][j] == 5:
            hsi_pic[i, j, :] = [1, 0, 1]
        if best_G[i][j] == 6:
            hsi_pic[i, j, :] = [1, 1, 0]
        if best_G[i][j] == 7:
            hsi_pic[i, j, :] = [0.5, 0.5, 1]

# utils.classification_map(hsi_pic[2:-2, 2:-2, :], best_G[2:-2, 2:-2], 24, "./classificationMap/indiana(target).png")




